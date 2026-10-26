#!/usr/bin/env python3
"""Analyze diagnostic FlashWeave reservation/worker traces without kernel access.

Only structured v1 events are accepted. Missing/lost/invalid evidence is not a
zero measurement. Event timestamps describe host decisions and model targets,
not actual NAND completion, nonpreemption or a drained maintenance queue.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

EVENTS = ("nand_schedule", "resource_schedule", "worker_decision")
ID, PPA, ZONE, READY, START, END, RESERVATION, COMPLETION = (1, 2, 4, 8, 16, 32, 64, 128)
PARENT_UNSUPPORTED, START_UNOBSERVED, STANDALONE, LUN_INCLUDES_CHANNEL = (1, 2, 4, 8)
LINE = re.compile(r"\[(?P<cpu>[0-9]+)\].*?\s(?P<seconds>[0-9]+)\.(?P<fraction>[0-9]+):\s+"
                  r"(?:flashweave:)?(?P<event>nand_schedule|resource_schedule|worker_decision):\s*(?P<payload>.*)$")
FIELD = re.compile(r"([a-z][a-z0-9_]*)=(-?(?:0x[0-9a-fA-F]+|[0-9]+))(?=\s|$)")
LOSS = re.compile(r"\bLOST\s+([0-9]+)\s+EVENTS\b|\bCPU:\s*[0-9]+.*\bLOST\b", re.I)
NAND_REQUIRED = set("schema call_ns issue_ns record_ns op_id ns_id ftl_id cmd type priority status valid_mask flags ch lun plane block page zone xfer_bytes cmd_ready_ns model_start_ns model_end_ns reservation_end_ns completion_target_ns old_hp_ns old_lp_ns old_normal_ns new_hp_ns new_lp_ns new_normal_ns".split())
RESOURCE_REQUIRED = set("schema op_id ns_id ftl_id resource segment_id call_ns issue_ns record_ns ready_ns model_start_ns model_end_ns old_hp_ns old_lp_ns old_normal_ns new_hp_ns new_lp_ns new_normal_ns valid_mask flags cmd type priority ch lun plane block page zone xfer_bytes".split())
WORKER_REQUIRED = set("schema call_ns record_ns op_id ns_id ftl_id family worker_kind decision site pass_index valid_mask flags phase_enabled pressure free_lines total_lines high_watermark low_watermark target_watermark gate tokens streak active_reads force_active budget_pages moved_pages page_bytes retired_pages_delta queue_result catchup_passes".split())
WORKER_DECISIONS = {0: "kick", 1: "yield", 2: "requeue", 3: "forced", 4: "emergency",
                    5: "pass_begin", 6: "pass_end", 7: "skip"}
EXAMPLE_LIMIT = 1000
LIMITATIONS = [
    "NAND/resource events record model scheduling decisions and reservation targets, not actual hardware issue/completion.",
    "Prior-LP-boundary crossings are reservation observations, not proof of physical preemption or a nonpreemption test pass.",
    "Host-request and worker-parent associations are unsupported by the v1 trace schema.",
    "T1 deterministic pending/ready injection, T4 bounded-progress scenarios and T5 emergency scenarios still require controlled integration experiments.",
    "No inference of complete maintenance drain or absence of future work follows from this trace.",
    "Trace overhead requires matched explicit diagnostic runs; do not silently enable this for the formal 20-run performance matrix.",
]


class BoundedExamples(list):
    """Count every observation while retaining a bounded set of raw examples."""
    def __init__(self):
        super().__init__()
        self.total_count = 0

    def append(self, value):
        self.total_count += 1
        if len(self) < EXAMPLE_LIMIT:
            super().append(value)

    def extend(self, values):
        for value in values:
            self.append(value)


def file_sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def parse_line(raw, number):
    match = LINE.search(raw)
    if not match:
        if any(name + ":" in raw for name in EVENTS):
            raise ValueError("unparseable FlashWeave event at line " + str(number))
        return None
    fields = {}
    payload = match["payload"]
    cursor = 0
    for field in FIELD.finditer(payload):
        if payload[cursor:field.start()].strip():
            raise ValueError("unparsed payload content at line " + str(number))
        key, value = field.groups()
        if key in fields:
            raise ValueError("duplicate field at line " + str(number) + ": " + key)
        fields[key] = int(value, 16 if "0x" in value.lower() else 10)
        cursor = field.end()
    if payload[cursor:].strip():
        raise ValueError("unparsed payload tail at line " + str(number))
    fraction = match["fraction"]
    if len(fraction) > 9:
        raise ValueError("trace timestamp has unsupported precision at line " + str(number))
    stamp = int(match["seconds"]) * 1000000000 + int(fraction.ljust(9, "0"))
    return {"event": match["event"], "cpu": int(match["cpu"]), "trace_time_ns": stamp,
            "line": number, "fields": fields}


def validate_fields(event):
    fields, name = event["fields"], event["event"]
    required = {"nand_schedule": NAND_REQUIRED, "resource_schedule": RESOURCE_REQUIRED,
                "worker_decision": WORKER_REQUIRED}[name]
    missing = sorted(required - set(fields))
    problems = []
    if missing:
        return ["line %d missing %s fields: %s" % (event["line"], name, ",".join(missing))]
    if fields["schema"] != 1:
        problems.append("line %d unsupported trace schema" % event["line"])
    for key, value in fields.items():
        if key.endswith("_ns") and value < 0:
            problems.append("line %d negative raw timestamp: %s" % (event["line"], key))
    if fields["record_ns"] < fields["call_ns"]:
        problems.append("line %d record_ns precedes call_ns" % event["line"])
    if fields["flags"] < 0 or fields["flags"] & ~15 or not fields["flags"] & PARENT_UNSUPPORTED:
        problems.append("line %d unsupported flags/parent contract" % event["line"])
    if name in ("nand_schedule", "resource_schedule"):
        mask = fields["valid_mask"]
        if mask < 0 or mask & ~255:
            problems.append("line %d unsupported valid_mask" % event["line"])
        if fields["op_id"] <= 0:
            problems.append("line %d invalid op_id" % event["line"])
        ready_key = "cmd_ready_ns" if name == "nand_schedule" else "ready_ns"
        if mask & READY and mask & START and fields["model_start_ns"] < fields[ready_key]:
            problems.append("line %d model start precedes ready" % event["line"])
        if mask & START and mask & END and fields["model_end_ns"] < fields["model_start_ns"]:
            problems.append("line %d model end precedes start" % event["line"])
        if name == "nand_schedule":
            if fields["priority"] not in (0, 1, 2) or fields["status"] not in (0, 1, 2, 3):
                problems.append("line %d unsupported priority/status" % event["line"])
            if mask & END and mask & COMPLETION and fields["completion_target_ns"] < fields["model_end_ns"]:
                problems.append("line %d completion target precedes modeled NAND end" % event["line"])
            scheduled_bits = READY | START | END | RESERVATION | COMPLETION
            if fields["status"] == 0 and mask & scheduled_bits != scheduled_bits:
                problems.append("line %d scheduled NAND interval/target validity is incomplete" % event["line"])
        else:
            flags = fields["flags"]
            if flags & START_UNOBSERVED and mask & START:
                problems.append("line %d unknown start incorrectly marked valid" % event["line"])
            if fields["resource"] not in (0, 1, 2) or fields["segment_id"] < 0:
                problems.append("line %d unsupported resource/segment" % event["line"])
    else:
        mask = fields["valid_mask"]
        if mask < 0 or mask & ~255 or fields["decision"] not in WORKER_DECISIONS:
            problems.append("line %d unsupported worker mask/decision" % event["line"])
        if mask & 8 and fields["decision"] == 6:
            if fields["moved_pages"] < 0 or fields["page_bytes"] <= 0:
                problems.append("line %d negative valid worker progress" % event["line"])
        if fields["retired_pages_delta"] != -1:
            problems.append("line %d v1 retired-pages field must be unsupported (-1)" % event["line"])
    return problems


def analyze_lines(lines, capture=None, state_path=None):
    # A file-backed index keeps multi-million-event diagnostic traces out of RAM.
    database = sqlite3.connect(str(state_path) if state_path else ":memory:")
    database.executescript("""
        CREATE TABLE ops (op TEXT PRIMARY KEY, ns INT, ftl INT, mask INT, status INT);
        CREATE TABLE resources (op TEXT, resource INT, segment INT, ns INT, ftl INT,
            mask INT, flags INT, cmd INT, type INT, line INT,
            PRIMARY KEY(op,resource,segment));
        CREATE TABLE worker_passes (op TEXT, pass INT, begins INT DEFAULT 0,
            ends INT DEFAULT 0, PRIMARY KEY(op,pass));
    """)
    counts = collections.Counter()
    resource_instances = collections.Counter()
    problems, losses, crossings = BoundedExamples(), BoundedExamples(), []
    status_counts = collections.Counter()
    workers = {"records": 0, "decision_counts": collections.Counter(),
               "retired_pages": None, "moved_pages": 0, "moved_mapping_bytes": 0, "progress_records": 0,
               "unknown_progress_records": 0, "queue_accepted": 0, "queue_rejected": 0,
               "queue_result_unavailable": 0}
    crossing_count = 0
    first, last, per_cpu_last = None, None, {}
    parsed = 0
    for number, raw in enumerate(lines, 1):
        if LOSS.search(raw):
            losses.append({"line": number, "text": raw.strip()})
        try:
            event = parse_line(raw, number)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if event is None:
            continue
        parsed += 1
        name, fields = event["event"], event["fields"]
        counts[name] += 1
        first = event["trace_time_ns"] if first is None else min(first, event["trace_time_ns"])
        last = event["trace_time_ns"] if last is None else max(last, event["trace_time_ns"])
        cpu = event["cpu"]
        if cpu in per_cpu_last and event["trace_time_ns"] < per_cpu_last[cpu]:
            problems.append("line %d trace time moves backwards on one CPU" % number)
        per_cpu_last[cpu] = event["trace_time_ns"]
        invalid = validate_fields(event)
        problems.extend(invalid)
        if invalid:
            continue
        if name == "nand_schedule":
            key = fields["op_id"]
            try:
                database.execute("INSERT INTO ops VALUES(?,?,?,?,?)", (str(key), fields["ns_id"],
                    fields["ftl_id"], fields["valid_mask"], fields["status"]))
                status_counts[str(fields["status"])] += 1
            except sqlite3.IntegrityError:
                problems.append("duplicate NAND op_id %d (loss/reload/collision)" % key)
            mask = fields["valid_mask"]
            if fields["priority"] == 1 and fields["status"] == 0 and mask & START and mask & RESERVATION:
                if fields["model_start_ns"] < fields["old_lp_ns"]:
                    crossing_count += 1
                    if len(crossings) < 100:
                        crossings.append({"op_id": key, "ns_id": fields["ns_id"], "ftl_id": fields["ftl_id"],
                       "line": number, "read_model_start_ns": fields["model_start_ns"],
                       "prior_lp_available_ns": fields["old_lp_ns"],
                       "new_lp_available_ns": fields["new_lp_ns"],
                       "interpretation": "read reservation starts before prior LP availability; already-issued state unobserved"})
        elif name == "resource_schedule":
            key = (fields["op_id"], fields["resource"], fields["segment_id"])
            try:
                database.execute("INSERT INTO resources VALUES(?,?,?,?,?,?,?,?,?,?)", (str(key[0]),
                    key[1], key[2], fields["ns_id"], fields["ftl_id"], fields["valid_mask"],
                    fields["flags"], fields["cmd"], fields["type"], event["line"]))
            except sqlite3.IntegrityError:
                problems.append("duplicate resource segment %s" % (key,))
            if fields["valid_mask"] & ID:
                if fields["resource"] == 2:
                    identity = "pcie:ns=%d" % fields["ns_id"]
                elif fields["valid_mask"] & PPA:
                    identity = "channel:ns=%d:ftl=%d:ch=%d" % (fields["ns_id"], fields["ftl_id"], fields["ch"])
                    if fields["resource"] == 0:
                        identity = "nand:ns=%d:ftl=%d:ch=%d:lun=%d" % (fields["ns_id"], fields["ftl_id"], fields["ch"], fields["lun"])
                else:
                    identity = "resource=%d:address_unobserved" % fields["resource"]
            else:
                identity = "resource=%d:identity_unobserved" % fields["resource"]
            resource_instances[identity] += 1
        else:
            observe_worker(workers, fields)
            if fields["decision"] in (5, 6):
                column = "begins" if fields["decision"] == 5 else "ends"
                database.execute("INSERT INTO worker_passes(op,pass," + column + ") VALUES(?,?,1) "
                    "ON CONFLICT(op,pass) DO UPDATE SET " + column + "=" + column + "+1",
                    (str(fields["op_id"]), fields["pass_index"]))
        if parsed % 10000 == 0:
            database.commit()

    database.commit()
    def scalar(query):
        return database.execute(query).fetchone()[0]
    linked = scalar("SELECT count(*) FROM resources r JOIN ops n ON r.op=n.op WHERE (r.flags & 4)=0")
    standalone = scalar("SELECT count(*) FROM resources WHERE (flags & 4)!=0")
    if scalar("SELECT count(*) FROM resources WHERE (flags & 4)!=0 AND (cmd!=-1 OR type!=-1)"):
        problems.append("standalone resource incorrectly carries NAND command identity")
    mismatch = scalar("SELECT count(*) FROM resources r JOIN ops n ON r.op=n.op WHERE (r.flags & 4)=0 AND (r.mask & 1)!=0 AND (n.mask & 1)!=0 AND (r.ns!=n.ns OR r.ftl!=n.ftl)")
    if mismatch:
        problems.append("resource identity differs from associated NAND operation")
    orphan_query = "SELECT r.op,r.resource,r.segment,r.line FROM resources r LEFT JOIN ops n ON r.op=n.op WHERE n.op IS NULL AND (r.flags & 4)=0"
    orphan_count = scalar("SELECT count(*) FROM (" + orphan_query + ")")
    orphaned = [{"op_id": int(row[0]), "resource": row[1], "segment_id": row[2], "line": row[3]}
                for row in database.execute(orphan_query + " LIMIT 100")]
    if orphan_count:
        problems.append("resource segments lack a NAND event (boundary/loss/incomplete association)")
    operations, segments = scalar("SELECT count(*) FROM ops"), scalar("SELECT count(*) FROM resources")
    worker_partial = scalar("SELECT count(*) FROM worker_passes WHERE begins!=1 OR ends!=1")
    if worker_partial:
        problems.append("worker pass begin/end records unpaired (capture boundary/loss/incomplete pass)")
    workers["pass_pairing"] = {"observed_passes": scalar("SELECT count(*) FROM worker_passes"),
                                "unpaired_or_duplicate": worker_partial}
    database.close()
    if losses:
        problems.append("trace text reports lost events")
    if parsed == 0:
        problems.append("empty FlashWeave event stream")
    if capture is None:
        problems.append("capture metadata absent; no-loss/module-lifecycle evidence is unverified")
    else:
        if capture.get("diagnostic_only") is not True:
            problems.append("diagnostic-only capture scope is unverified")
        if capture.get("status") != "captured" or capture.get("incomplete") is not False:
            problems.append("capture reports failure or incomplete stream")
        if capture.get("loss", {}).get("verified_no_loss") is not True:
            problems.append("per-CPU no-loss evidence is missing or failed")
        if capture.get("global_tracer_modified") is not False:
            problems.append("private-instance isolation is unverified")
        if capture.get("event_identity_start") != capture.get("event_identity_stop"):
            problems.append("event identity changed during capture (module lifecycle)")
        if set(capture.get("event_identity_start", {})) != set(EVENTS):
            problems.append("three registered event identities were not captured")
    return {
        "schema": "flashweave-trace-analysis-v1", "status": "valid_reservation_observations" if not problems else "incomplete_or_invalid",
        "problems": list(problems), "problem_count_total": problems.total_count,
        "problem_examples_limit": EXAMPLE_LIMIT,
        "problem_examples_truncated": problems.total_count > len(problems),
        "event_counts": dict(counts),
        "trace_span_ns": None if first is None else last - first,
        "raw_lost_markers": list(losses), "raw_lost_marker_count_total": losses.total_count,
        "raw_lost_marker_examples_truncated": losses.total_count > len(losses),
        "unique_nand_operations": operations,
        "resource_associations": {"linked_segments": linked, "standalone_segments": standalone,
                                  "orphaned_count": orphan_count, "orphaned_examples": orphaned, "unique_segments": segments},
        "resource_instance_event_counts": dict(resource_instances),
        "resource_identity_scope": "NAND=(ns,ftl,ch,lun); channel=(ns,ftl,ch); PCIe shared within ns, not per FTL",
        "nand_status_counts": dict(status_counts),
        "prior_lp_boundary_crossings": {"count": crossing_count, "examples": crossings,
            "not_actual_inflight_or_nonpreemption_evidence": True},
        "worker_summary": worker_summary(workers),
        "operation_conservation_scope": "one schedule record per observed NAND op and typed resource association; actual completion conservation is unsupported",
        "full_model_drain_verified": False, "physical_nonpreemption_verified": False,
        "limitations": LIMITATIONS,
    }


def observe_worker(summary, fields):
    summary["records"] += 1
    action = WORKER_DECISIONS[fields["decision"]]
    summary["decision_counts"][action] += 1
    if fields["decision"] == 6:
        if fields["valid_mask"] & 8:
            summary["progress_records"] += 1
            summary["moved_pages"] += fields["moved_pages"]
            summary["moved_mapping_bytes"] += fields["moved_pages"] * fields["page_bytes"]
        else:
            summary["unknown_progress_records"] += 1
    if fields["decision"] in (0, 2):
        if fields["valid_mask"] & 16:
            summary["queue_accepted" if fields["queue_result"] else "queue_rejected"] += 1
        else:
            summary["queue_result_unavailable"] += 1


def worker_summary(summary):
    result = dict(summary)
    result["decision_counts"] = dict(summary["decision_counts"])
    for name in ("yield", "requeue", "forced", "emergency"):
        result[name + "_decisions"] = summary["decision_counts"][name]
    if not summary["progress_records"]:
        result["moved_pages"] = None
        result["moved_mapping_bytes"] = None
    result["retired_pages"] = None
    result["retired_pages_status"] = "unsupported_by_v1_schema"
    result["scope"] = "observed decisions and valid pass-end migration return values; moved pages are not retired debt or bounded-progress proof"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", required=True, help="Directory containing trace.txt, result.json and event formats")
    parser.add_argument("--out", required=True, help="Fresh analysis directory outside the capture")
    args = parser.parse_args()
    capture_dir, out = Path(args.capture).resolve(), Path(args.out).resolve()
    if out.exists() or out.is_symlink() or out == capture_dir or capture_dir in out.parents:
        parser.error("--out must be a fresh directory outside the immutable capture")
    out.mkdir(parents=True)
    capture = None
    errors = BoundedExamples()
    try:
        capture = json.loads((capture_dir / "result.json").read_text())
        inventory = capture.get("artifacts_sha256", {})
        required_artifacts = ["trace.txt", "per_cpu_before.json", "per_cpu_after.json"]
        required_artifacts += ["event_formats/" + name + ".format.txt" for name in EVENTS]
        for required in required_artifacts:
            if required not in inventory:
                errors.append("required capture artifact not inventoried: " + required)
        for relative, expected in inventory.items():
            path = capture_dir / relative
            if capture_dir not in path.resolve().parents or not path.is_file() or file_sha256(path) != expected:
                errors.append("capture artifact missing/changed: " + relative)
        for name in EVENTS:
            path = capture_dir / "event_formats" / (name + ".format.txt")
            if not path.is_file():
                errors.append("event format missing: " + name)
            elif file_sha256(path) != capture.get("event_identity_start", {}).get(name, {}).get("format_sha256"):
                errors.append("event format differs from registered identity: " + name)
        from trace_capture import loss_summary
        recomputed = loss_summary(json.loads((capture_dir / "per_cpu_before.json").read_text()),
                                  json.loads((capture_dir / "per_cpu_after.json").read_text()))
        if recomputed != capture.get("loss"):
            errors.append("loss summary differs from saved per-CPU evidence")
        if recomputed["verified_no_loss"] is not True:
            errors.append("saved per-CPU evidence does not verify no loss")
        with (capture_dir / "trace.txt").open(errors="strict") as stream:
            result = analyze_lines(stream, capture, out / "_analysis_state.sqlite")
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        result = analyze_lines([], capture)
        errors.append(type(exc).__name__ + ": " + str(exc))
    # Preserve the full parser error count even if its example buffer is full;
    # CLI errors also have their own bounded examples so they remain visible.
    result["cli_error_count_total"] = errors.total_count
    result["cli_error_examples"] = list(errors)
    result["cli_error_examples_truncated"] = errors.total_count > len(errors)
    result["problem_count_total"] += errors.total_count
    result["problems"] = (result["problems"] + list(errors))[:EXAMPLE_LIMIT]
    result["problem_examples_truncated"] = result["problem_count_total"] > len(result["problems"])
    if result["problem_count_total"]:
        result["status"] = "incomplete_or_invalid"
    result["capture_directory"] = str(capture_dir)
    result["capture_metadata_sha256"] = file_sha256(capture_dir / "result.json") if (capture_dir / "result.json").is_file() else None
    state_file = out / "_analysis_state.sqlite"
    if state_file.exists():
        state_file.unlink()
    (out / "analysis.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    text = ["Diagnostic trace analysis: " + result["status"], "",
            "Problems (total): " + str(result["problem_count_total"]),
            "CLI/integrity errors (total): " + str(result["cli_error_count_total"]),
            "Raw lost-event markers (total): " + str(result["raw_lost_marker_count_total"]),
            "Example limit per list: " + str(EXAMPLE_LIMIT),
            "Event counts: " + json.dumps(result["event_counts"], sort_keys=True),
            "Worker summary: " + json.dumps(result["worker_summary"], sort_keys=True),
            "Prior LP boundary crossings: " + str(result["prior_lp_boundary_crossings"]["count"]),
            "This does not verify actual NAND completion, nonpreemption or full maintenance drain.", "",
            *["PROBLEM: " + value for value in result["problems"]], "",
            *["CLI/INTEGRITY: " + value for value in result["cli_error_examples"]], "",
            *["LIMITATION: " + value for value in LIMITATIONS]]
    (out / "analysis.txt").write_text("\n".join(text) + "\n")
    print(json.dumps({"status": result["status"], "out": str(out),
                      "problem_count_total": result["problem_count_total"],
                      "cli_error_count_total": result["cli_error_count_total"],
                      "problems": result["problems"]}, sort_keys=True))
    return 0 if not result["problem_count_total"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
