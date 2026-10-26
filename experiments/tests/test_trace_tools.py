"""Synthetic raw-format fixtures only. No tracefs, module, root or device access."""
import copy
import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

EXPERIMENTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXPERIMENTS))
import trace_analyze as analyze
import trace_capture as capture

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def metadata():
    raw = (FIXTURES / "cpu_before.stats").read_text()
    stats = {"cpu0": capture.parse_cpu_stats(raw)}
    identity = {name: {"id": 500 + index, "format_sha256": "synthetic"}
                for index, name in enumerate(capture.EVENTS)}
    return {"status": "captured", "incomplete": False, "diagnostic_only": True,
            "global_tracer_modified": False, "loss": capture.loss_summary(stats, stats),
            "event_identity_start": identity, "event_identity_stop": copy.deepcopy(identity)}


def raw_fixture():
    return (FIXTURES / "reservation_v1.trace").read_text()


def replace_field(raw, event, occurrence, key, value):
    lines = raw.splitlines(keepends=True)
    matches = [index for index, line in enumerate(lines) if ": " + event + ":" in line]
    index = matches[occurrence]
    lines[index], count = re.subn(r"\b" + re.escape(key) + r"=-?[0-9]+(?=\s|$)",
                                 key + "=" + str(value), lines[index])
    if count != 1:
        raise AssertionError("fixture mutation did not select one raw field")
    return "".join(lines)


class RawTraceFixtureTests(unittest.TestCase):
    def observed(self, raw=None, meta=None):
        return analyze.analyze_lines((raw if raw is not None else raw_fixture()).splitlines(),
                                     metadata() if meta is None else meta)

    def test_reservation_bypass_is_an_observation_not_hardware_proof(self):
        result = self.observed()
        self.assertEqual(result["status"], "valid_reservation_observations", result["problems"])
        self.assertEqual(result["unique_nand_operations"], 2)
        self.assertEqual(result["resource_associations"]["linked_segments"], 3)
        self.assertEqual(result["prior_lp_boundary_crossings"]["count"], 1)
        self.assertFalse(result["physical_nonpreemption_verified"])
        self.assertFalse(result["full_model_drain_verified"])
        worker = result["worker_summary"]
        self.assertEqual(worker["yield_decisions"], 1)
        self.assertEqual(worker["requeue_decisions"], 1)
        self.assertEqual(worker["forced_decisions"], 1)
        self.assertEqual(worker["moved_pages"], 32)
        self.assertEqual(worker["moved_mapping_bytes"], 131072)
        self.assertIsNone(worker["retired_pages"])

    def test_credit_model_unknown_start_is_not_a_zero_start_measurement(self):
        event = next(analyze.parse_line(line, n) for n, line in enumerate(raw_fixture().splitlines(), 1)
                     if "resource_schedule:" in line and "flags=3 " in line)
        self.assertEqual(event["fields"]["model_start_ns"], 0)
        self.assertEqual(analyze.validate_fields(event), [])
        bad = replace_field(raw_fixture(), "resource_schedule", 1, "valid_mask", 127)
        self.assertTrue(any("incorrectly marked valid" in value for value in self.observed(bad)["problems"]))

    def test_negative_raw_model_timestamp_is_rejected(self):
        raw = replace_field(raw_fixture(), "nand_schedule", 1, "model_start_ns", -100000)
        result = self.observed(raw)
        self.assertEqual(result["status"], "incomplete_or_invalid")
        self.assertTrue(any("negative raw timestamp" in value for value in result["problems"]))

    def test_end_before_start_is_not_silently_clamped(self):
        raw = replace_field(raw_fixture(), "nand_schedule", 1, "model_end_ns", 1)
        self.assertTrue(any("end precedes start" in value for value in self.observed(raw)["problems"]))

    def test_empty_trace_cannot_pass(self):
        result = self.observed((FIXTURES / "empty.trace").read_text())
        self.assertIn("empty FlashWeave event stream", result["problems"])

    def test_raw_overflow_marker_and_missing_loss_evidence_fail_closed(self):
        result = self.observed(raw_fixture() + "CPU:0 [LOST 17 EVENTS]\n")
        self.assertTrue(result["raw_lost_markers"])
        self.assertEqual(result["status"], "incomplete_or_invalid")
        meta = metadata()
        meta.pop("loss")
        self.assertTrue(any("no-loss" in value for value in self.observed(meta=meta)["problems"]))

    def test_duplicate_ids_and_resource_orphans_expose_reload_or_boundary(self):
        raw = replace_field(raw_fixture(), "nand_schedule", 1, "op_id", 1)
        result = self.observed(raw)
        self.assertTrue(any("duplicate NAND" in value for value in result["problems"]))
        self.assertEqual(result["resource_associations"]["orphaned_count"], 2)

    def test_unsupported_schema_and_missing_fields_are_not_zero_filled(self):
        raw = replace_field(raw_fixture(), "nand_schedule", 0, "schema", 2)
        self.assertTrue(any("unsupported trace schema" in value for value in self.observed(raw)["problems"]))
        raw = raw_fixture().replace(" completion_target_ns=740000", "", 1)
        self.assertTrue(any("missing nand_schedule" in value for value in self.observed(raw)["problems"]))

    def test_worker_retired_sentinel_and_partial_passes(self):
        raw = replace_field(raw_fixture(), "worker_decision", 4, "retired_pages_delta", 32)
        self.assertTrue(any("retired-pages" in value for value in self.observed(raw)["problems"]))
        raw = "\n".join(line for line in raw_fixture().splitlines() if "decision=5 " not in line)
        self.assertEqual(self.observed(raw)["worker_summary"]["pass_pairing"]["unpaired_or_duplicate"], 1)

    def test_capture_limit_failure_cannot_pass_parser(self):
        meta = metadata()
        meta.update(status="capture_failed", incomplete=True, problems=["max_bytes_reached"])
        self.assertEqual(self.observed(meta=meta)["status"], "incomplete_or_invalid")

    def test_large_malformed_stream_counts_all_errors_with_bounded_examples(self):
        line = next(line for line in raw_fixture().splitlines()
                    if "nand_schedule:" in line)
        malformed = line.replace(" completion_target_ns=740000", "")
        result = self.observed((malformed + "\n") * 2500)
        self.assertEqual(result["status"], "incomplete_or_invalid")
        self.assertEqual(result["problem_count_total"], 2500)
        self.assertEqual(len(result["problems"]), 1000)
        self.assertTrue(result["problem_examples_truncated"])
        self.assertEqual(result["event_counts"]["nand_schedule"], 2500)
        overflow = self.observed(raw_fixture() + "CPU:0 [LOST 17 EVENTS]\n" * 2500)
        self.assertEqual(overflow["raw_lost_marker_count_total"], 2500)
        self.assertEqual(len(overflow["raw_lost_markers"]), 1000)
        self.assertTrue(overflow["raw_lost_marker_examples_truncated"])

    def test_current_trace_header_field_contract_matches_parser(self):
        header = (EXPERIMENTS.parent / "flashweave_trace.h").read_text()
        for name, fields in (("nand_schedule", analyze.NAND_REQUIRED),
                             ("resource_schedule", analyze.RESOURCE_REQUIRED),
                             ("worker_decision", analyze.WORKER_REQUIRED)):
            match = re.search(r"TRACE_EVENT\(" + name + r",.*?TP_printk\(\"([^\"]+)\"", header, re.S)
            self.assertIsNotNone(match, name)
            printed = set(re.findall(r"([a-z][a-z0-9_]*)=%", match.group(1))) | {"schema"}
            self.assertEqual(printed, fields, name)


class CaptureEvidenceFixtureTests(unittest.TestCase):
    def test_overflow_and_counter_reset_are_errors(self):
        before = {"cpu0": capture.parse_cpu_stats((FIXTURES / "cpu_before.stats").read_text())}
        overflow = {"cpu0": capture.parse_cpu_stats((FIXTURES / "cpu_overflow.stats").read_text())}
        result = capture.loss_summary(before, overflow)
        self.assertFalse(result["verified_no_loss"])
        self.assertEqual(result["per_cpu_deltas"]["cpu0"]["overrun"], 17)
        reset = capture.loss_summary(overflow, before)
        self.assertTrue(any("reset/reload" in value for value in reset["problems"]))

    def test_missing_stats_are_unknown_not_zero(self):
        partial = {"cpu0": capture.parse_cpu_stats("overrun: 0\n")}
        self.assertFalse(capture.loss_summary(partial, partial)["verified_no_loss"])
        self.assertFalse(capture.loss_summary({}, {})["verified_no_loss"])

    def test_tail_entries_after_stop_make_stream_incomplete(self):
        raw = (FIXTURES / "cpu_before.stats").read_text()
        before = {"cpu0": capture.parse_cpu_stats(raw)}
        after = {"cpu0": capture.parse_cpu_stats(raw.replace("entries: 0", "entries: 3")
                                                   .replace("read events: 0", "read events: 101"))}
        result = capture.loss_summary(before, after)
        self.assertFalse(result["verified_no_loss"])
        self.assertEqual(result["per_cpu_entries_after"]["cpu0"], 3)
        self.assertEqual(result["per_cpu_read_events"]["cpu0"], {"before": 0, "after": 101})
        self.assertTrue(any("entries remain" in value for value in result["problems"]))

    def test_module_unload_or_reload_breaks_identity(self):
        initial = metadata()["event_identity_start"]
        current = copy.deepcopy(initial)
        current["nand_schedule"]["id"] += 1
        self.assertTrue(capture.validate_event_identity(initial, current))
        current.pop("worker_decision")
        self.assertTrue(any("disappeared" in value for value in capture.validate_event_identity(initial, current)))

    def test_live_capture_root_gate_can_be_checked_without_sysfs(self):
        with patch.object(capture.platform, "system", return_value="Darwin"):
            with self.assertRaisesRegex(RuntimeError, "Linux root"):
                capture.require_root()

    def test_instance_creation_collision_never_cleans_up_someone_elses_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / "output"
            out.mkdir()
            tracefs = root / "synthetic_tracefs"
            token = "a" * 32
            existing = tracefs / "instances" / ("fw_diag_" + token[:16])
            existing.mkdir(parents=True)
            controls = {"tracing_on": "1\n", "buffer_size_kb": "777\n",
                        "current_tracer": "function\n", "trace": "existing captured data\n"}
            for name, value in controls.items():
                (existing / name).write_text(value)
            for name in capture.EVENTS:
                path = existing / "events" / capture.GROUP / name
                path.mkdir(parents=True)
                (path / "enable").write_text("1\n")
                controls["events/" + capture.GROUP + "/" + name + "/enable"] = "1\n"
            before = {name: (existing / name).read_bytes() for name in controls}
            args = argparse.Namespace(out=str(out), token=token, tracefs=None,
                                      buffer_kb=1024, max_bytes=1024**3, max_seconds=60)
            with patch.object(capture, "require_root"), \
                 patch.object(capture, "find_tracefs", return_value=tracefs), \
                 patch.object(capture, "event_identity", return_value=metadata()["event_identity_start"]), \
                 patch.object(capture.signal, "signal"), \
                 patch.object(Path, "rmdir") as rmdir:
                status = capture.serve(args)
                rmdir.assert_not_called()
            self.assertEqual(status, 2)
            self.assertTrue(existing.is_dir())
            self.assertEqual(before, {name: (existing / name).read_bytes() for name in controls})
            report = json.loads((out / "result.json").read_text())
            self.assertFalse(report["private_instance_created"])
            self.assertTrue(any("FileExistsError" in value for value in report["problems"]))

    def test_cli_analysis_preserves_raw_and_refuses_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "capture"
            raw.mkdir()
            (raw / "trace.txt").write_text(raw_fixture())
            (raw / "event_formats").mkdir()
            meta = metadata()
            for name in capture.EVENTS:
                path = raw / "event_formats" / (name + ".format.txt")
                path.write_text("synthetic parser fixture format for " + name)
                digest = analyze.file_sha256(path)
                meta["event_identity_start"][name]["format_sha256"] = digest
                meta["event_identity_stop"][name]["format_sha256"] = digest
            stats = {"cpu0": capture.parse_cpu_stats((FIXTURES / "cpu_before.stats").read_text())}
            for label in ("before", "after"):
                (raw / ("per_cpu_" + label + ".json")).write_text(json.dumps(stats))
            meta["artifacts_sha256"] = {str(path.relative_to(raw)): analyze.file_sha256(path)
                for path in raw.rglob("*") if path.is_file()}
            (raw / "result.json").write_text(json.dumps(meta))
            before = analyze.file_sha256(raw / "trace.txt")
            out = root / "analysis"
            cmd = [sys.executable, str(EXPERIMENTS / "trace_analyze.py"), "--capture", str(raw), "--out", str(out)]
            run = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertEqual(analyze.file_sha256(raw / "trace.txt"), before)
            self.assertFalse((out / "_analysis_state.sqlite").exists())
            repeated = subprocess.run(cmd, capture_output=True, text=True)
            self.assertNotEqual(repeated.returncode, 0)
            # A changed trace must fail rather than recompute and silently bless it.
            (raw / "trace.txt").write_text(raw_fixture() + "CPU:0 [LOST 17 EVENTS]\n")
            bad = subprocess.run(cmd[:-1] + [str(root / "analysis_changed")], capture_output=True, text=True)
            self.assertEqual(bad.returncode, 2)
            self.assertIn("changed", (root / "analysis_changed/analysis.txt").read_text())
            # Full parser buffers must not hide a simultaneous CLI/hash error.
            line = next(line for line in raw_fixture().splitlines() if "nand_schedule:" in line)
            malformed = line.replace(" completion_target_ns=740000", "")
            (raw / "trace.txt").write_text((malformed + "\n") * 2500)
            large = subprocess.run(cmd[:-1] + [str(root / "analysis_large")], capture_output=True, text=True)
            self.assertEqual(large.returncode, 2)
            report = json.loads((root / "analysis_large/analysis.json").read_text())
            self.assertEqual(len(report["problems"]), 1000)
            self.assertEqual(report["problem_count_total"], 2501)
            self.assertEqual(report["cli_error_count_total"], 1)
            self.assertTrue(any("missing nand_schedule" in value for value in report["problems"]))
            self.assertTrue(any("changed" in value for value in report["cli_error_examples"]))


if __name__ == "__main__":
    unittest.main()
