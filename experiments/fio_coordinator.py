#!/usr/bin/env python3
"""Run generated fio stages with synchronous, whitelisted Python control steps.

This replaces fio 3.16 shell hooks, whose return/status semantics are unsuitable
for the protocol barrier. --plan-only only parses and writes local files.
No module is loaded/unloaded by this helper. The upstream runner owns its life.
"""
import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

PHASE = "/sys/kernel/debug/nvmev/ftl0/test_phase"
HEAT = "/sys/kernel/debug/nvmev/ftl0/heat_epoch"
VARIANTS = {"die_latency1_norp_sb", "die_latency1_qlc_all_norp_sb",
            "die_latency3_norp_sb", "die_latency3_qlc_all_norp_sb"}


def fail(message):
    raise RuntimeError(message)


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def parse_job(path):
    """Strictly parse this pinned runner's simple, generated fio format."""
    global_options = {}
    jobs = []
    current = None
    for number, raw in enumerate(Path(path).read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1]
            if not re.fullmatch(r"[A-Za-z0-9_]+", name):
                fail("unsupported section name at line " + str(number))
            if name == "global":
                if current is not None or global_options:
                    fail("global section must appear once before jobs")
                current = global_options
            else:
                if any(j["name"] == name for j in jobs):
                    fail("duplicate job name: " + name)
                current = {}
                jobs.append({"name": name, "options": current})
            continue
        if current is None:
            fail("option outside section at line " + str(number))
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not re.fullmatch(r"[a-zA-Z0-9_]+", key) or key in current:
            fail("duplicate/unsupported option at line " + str(number))
        if not separator and key != "stonewall":
            fail("unsupported flag: " + key)
        current[key] = value if separator else None
    if not jobs:
        fail("no generated fio jobs")
    if any(k.startswith("exec_") for k in global_options):
        fail("global shell hooks are forbidden")
    if global_options.get("randrepeat") != "1":
        fail("protocol requires randrepeat=1")
    if not re.fullmatch(r"[1-9][0-9]*", global_options.get("randseed", "")):
        fail("protocol requires an explicit positive randseed")
    if not re.fullmatch(r"/dev/nvme[0-9]+n[0-9]+", global_options.get("filename", "")):
        fail("protocol requires one whole NVMe namespace")
    return global_options, jobs


def parse_hook(command):
    match = re.fullmatch(r"echo[ \t]+([01])[ \t]+>[ \t]+"
                         r"(/sys/kernel/debug/nvmev/ftl0/(?:test_phase|heat_epoch))",
                         command)
    if not match:
        fail("non-whitelisted shell hook: " + command)
    value, path = match.groups()
    if path == HEAT and value != "1":
        fail("heat_epoch only accepts the pinned +1 pulse")
    return {"path": path, "value": int(value),
            "semantics": "increment" if path == HEAT else "set-and-readback"}


def build_plan(global_options, jobs):
    stages = []
    base_seed = int(global_options["randseed"])
    if base_seed + len(jobs) - 1 > (1 << 64) - 1:
        fail("derived job seed exceeds unsigned 64-bit range")
    for original_index, job in enumerate(jobs):
        options = job["options"]
        if not stages or "stonewall" in options:
            stages.append({"jobs": [], "controls_before": []})
        if "stonewall" in options and options["stonewall"] not in (None, "1"):
            fail("unsupported stonewall value")
        hooks = [key for key in options if key.startswith("exec_")]
        if any(key != "exec_prerun" for key in hooks):
            fail("only whitelisted exec_prerun control writes are accepted")
        if hooks:
            if stages[-1]["jobs"]:
                fail("control writes must begin their own sequential stage")
            stages[-1]["controls_before"].append(parse_hook(options["exec_prerun"]))
        clean = {key: value for key, value in options.items()
                 if key != "stonewall" and not key.startswith("exec_")}
        if "randseed" in clean:
            fail("unexpected per-job seed in generated input")
        # Splitting a file changes fio's internal job-index mixing. Make the
        # new protocol's per-job seeds explicit and stable across all cells.
        actual_seed = base_seed + original_index
        clean["randseed"] = str(actual_seed)
        stages[-1]["jobs"].append({"name": job["name"], "options": clean,
             "original_job_index": original_index, "randseed": actual_seed})
    measured = [index for index, stage in enumerate(stages)
                if any(job["name"].startswith("measure_") for job in stage["jobs"])]
    if measured != [len(stages) - 1]:
        fail("measurement must occupy exactly the final stage")
    measurement = stages[-1]
    if [job["name"] for job in measurement["jobs"]] != ["measure_reads", "measure_writes"]:
        fail("measured reads and writes must share one concurrent fio invocation")
    if measurement["controls_before"]:
        fail("measurement stage must not contain a shell control hook")
    for job, wanted in zip(measurement["jobs"], ("randread", "randwrite")):
        if job["options"].get("rw") != wanted or job["options"].get("numjobs") != "1":
            fail("unexpected measured operation or cloned jobs")
    markers = [index for index, stage in enumerate(stages)
               if any(job["name"] == "enter_test_phase" for job in stage["jobs"])]
    if markers != [len(stages) - 2]:
        fail("entry marker must be a separate stage immediately before measurement")
    marker = stages[markers[0]]
    if len(marker["jobs"]) != 1 or marker["controls_before"] != [parse_hook("echo 1 > " + PHASE)]:
        fail("invalid phase-entry marker")
    read_log = measurement["jobs"][0]["options"].get("write_lat_log", "")
    if not read_log.endswith("_read"):
        fail("cannot derive the pinned baseline directory from read log prefix")
    baseline = str(Path(read_log[:-5] + ".baseline").resolve())
    for index, stage in enumerate(stages):
        stage["index"] = index
        stage["concurrent_jobs"] = [job["name"] for job in stage["jobs"]]
        stage["baseline_after"] = baseline if index == markers[0] else None
    return {"schema_version": 1,
            "scope": "static stage plan; no kernel or device execution is certified",
            "randseed": int(global_options["randseed"]),
            "rng_semantics": "NEW protocol: explicit job randseed = block seed + zero-based original job index; randrepeat=1. fio-3.16 internal stream derivation remains version-specific. Not a historical archive replay.",
            "device": global_options["filename"],
            "baseline_directory": baseline,
            "measurement_stage": len(stages) - 1,
            "stages": stages}


def write_stage_job(path, global_options, stage):
    lines = ["; No fio exec hooks: controls are synchronous Python steps.", "[global]"]
    lines += [key + "=" + value for key, value in global_options.items()]
    for job in stage["jobs"]:
        lines += ["", "[" + job["name"] + "]"]
        lines += [key + "=" + value for key, value in job["options"].items()]
    text = "\n".join(lines) + "\n"
    if re.search(r"(?m)^\s*exec_", text):
        fail("internal error: an exec hook survived stage serialization")
    Path(path).write_text(text)


def expected_bytes(raw):
    match = re.fullmatch(r"([0-9]+)([kKmMgGtT]?)", raw)
    if not match:
        fail("unsupported generated I/O size: " + raw)
    return int(match[1]) * {"": 1, "k": 1024, "m": 1024**2,
                            "g": 1024**3, "t": 1024**4}[match[2].lower()]


def validate_stage_volume(global_options, stage, observed):
    for generated, result in zip(stage["jobs"], observed):
        options = {**global_options, **generated["options"]}
        direction = {"read": "read", "randread": "read", "write": "write",
                     "randwrite": "write"}.get(options.get("rw"))
        if direction is None or options.get("numjobs", "1") != "1":
            fail("unsupported generated operation/clone count")
        expected = expected_bytes(options["io_size"])
        actual = result.get(direction, {}).get("io_bytes")
        if actual is None or int(actual) != expected:
            fail("short/incomplete fio stage I/O: " + generated["name"])


def apply_control(control):
    path = Path(control["path"])
    if not path.is_file():
        fail("required control absent: " + str(path))
    before = path.read_text().strip()
    if not re.fullmatch(r"[0-9]+", before):
        fail("non-numeric control readback: " + str(path))
    path.write_text(str(control["value"]) + "\n")
    after = path.read_text().strip()
    expected = int(before) + 1 if control["semantics"] == "increment" else control["value"]
    if after != str(expected):
        fail("control readback mismatch: " + str(path))
    return {**control, "before": before, "after": after, "time_ns": time.time_ns()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job")
    parser.add_argument("--output", required=True)
    parser.add_argument("--output-format", choices=["json+"], default="json+")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    stage_root = output.parent / (output.stem + ".stages")
    if output.exists() or stage_root.exists():
        fail("coordinator output already exists; use a new attempt directory")
    global_options, jobs = parse_job(args.job)
    plan = build_plan(global_options, jobs)
    trace_mode = os.environ.get("FW_TRACE_MODE", "off")
    if trace_mode not in ("off", "diag"):
        fail("unsupported trace mode")
    trace_dir = str(Path(os.environ["FW_PROTOCOL_CASE_DIR"]).resolve() / "trace") if trace_mode == "diag" else None
    plan["trace_mode"] = trace_mode
    plan["trace_start_after_successful_baseline"] = trace_dir
    limit = int(os.environ.get("FW_TRACE_MAX_BYTES", str(8*1024**3)))
    if limit < 1: fail("trace byte limit must be positive")
    plan["trace_max_bytes"] = limit if trace_mode == "diag" else None
    stage_root.mkdir(parents=True)
    shutil_source = Path(args.job).read_bytes()
    (stage_root / "generated-original.fio").write_bytes(shutil_source)
    plan["generated_job_sha256"] = hashlib.sha256(shutil_source).hexdigest()
    for stage in plan["stages"]:
        folder = stage_root / ("stage_%03d" % stage["index"])
        folder.mkdir()
        stage["directory"] = str(folder)
        write_stage_job(folder / "job.fio", global_options, stage)
    save(stage_root / "plan.json", plan)
    if args.plan_only:
        save(stage_root / "status.json", {"status": "static_plan_only",
             "stages": len(plan["stages"]), "kernel_or_device_execution": False})
        print("STATIC PLAN ONLY:", stage_root / "plan.json")
        return
    if platform.system() != "Linux" or os.geteuid() != 0:
        fail("coordinator execution requires Linux root; use --plan-only for inspection")
    binary = Path(os.environ.get("FW_PROTOCOL_FIO", ""))
    if not binary.is_absolute() or not binary.is_file():
        fail("FW_PROTOCOL_FIO must be the checked absolute fio binary")
    variant = os.environ.get("FW_PROTOCOL_VARIANT", "")
    if variant not in VARIANTS:
        fail("unknown protocol variant")
    helper = Path(__file__).resolve().parent / "protocol_hook.py"
    if not helper.is_file():
        fail("staged protocol_hook.py absent")
    documents = []
    completed = []
    status = {"status": "running", "completed_stage_indices": completed,
              "baseline_succeeded": False,
              "scope": "stage execution; this does not certify Linux device behavior"}
    save(stage_root / "status.json", status)
    try:
        for stage in plan["stages"]:
            folder = Path(stage["directory"])
            evidence = []
            for control in stage["controls_before"]:
                evidence.append(apply_control(control))
                save(folder / "controls.json", evidence)
            if stage["index"] == plan["measurement_stage"] and not status["baseline_succeeded"]:
                fail("baseline has not succeeded; measurement is forbidden")
            cmd = [str(binary), str(folder / "job.fio"),
                   "--output=" + str(folder / "result.json"), "--output-format=json+"]
            save(folder / "command.json", {"argv": cmd, "time_started_ns": time.time_ns()})
            with (folder / "stdout.txt").open("w") as stdout, (folder / "stderr.txt").open("w") as stderr:
                result = subprocess.run(cmd, stdout=stdout, stderr=stderr)
            save(folder / "exit.json", {"returncode": result.returncode, "time_finished_ns": time.time_ns()})
            if result.returncode:
                fail("fio stage failed: " + str(stage["index"]))
            data = json.loads((folder / "result.json").read_text())
            observed = data.get("jobs", [])
            if [job.get("jobname") for job in observed] != stage["concurrent_jobs"]:
                fail("fio result names differ from stage plan")
            if any(job.get("error") != 0 for job in observed):
                fail("fio stage has job errors or missing error status")
            validate_stage_volume(global_options, stage, observed)
            documents.append(data)
            if stage["baseline_after"]:
                hook_cmd = [sys.executable, str(helper), "baseline", stage["baseline_after"],
                            variant, plan["device"].removeprefix("/dev/")]
                with (folder / "baseline.stdout.txt").open("w") as stdout, (folder / "baseline.stderr.txt").open("w") as stderr:
                    baseline_result = subprocess.run(hook_cmd, stdout=stdout, stderr=stderr)
                save(folder / "baseline-exit.json", {"argv": hook_cmd,
                     "returncode": baseline_result.returncode, "time_ns": time.time_ns()})
                if baseline_result.returncode:
                    fail("baseline helper failed; measurement is forbidden")
                snapshot = Path(stage["baseline_after"]) / "snapshot.json"
                captured = json.loads(snapshot.read_text())
                if captured.get("event") != "baseline" or captured.get("status") != "ok":
                    fail("baseline success snapshot missing/invalid")
                status["baseline_succeeded"] = True
                if trace_mode == "diag":
                    capture = Path(__file__).resolve().parent / "trace_capture.py"
                    if not capture.is_file(): fail("native trace collector absent")
                    trace_cmd = [sys.executable, str(capture), "start", "--out", trace_dir,
                                 "--max-bytes", str(limit)]
                    with (folder / "trace-start.stdout.txt").open("w") as stdout, (folder / "trace-start.stderr.txt").open("w") as stderr:
                        started = subprocess.run(trace_cmd, stdout=stdout, stderr=stderr)
                    if started.returncode: fail("trace start failed; measurement is forbidden")
                    status["diagnostic_trace_started"] = True
            completed.append(stage["index"])
            save(stage_root / "status.json", status)
        merged = dict(documents[0])
        merged["jobs"] = [job for document in documents for job in document["jobs"]]
        merged["disk_util"] = []
        merged["protocol_coordinator"] = {"schema_version": 1,
             "note": "Jobs concatenated from separate fio invocations; top-level timing/disk-util is not a whole-run aggregate.",
             "plan": str(stage_root / "plan.json"),
             "stage_results": [str(Path(stage["directory"]) / "result.json") for stage in plan["stages"]]}
        save(output, merged)
        status["status"] = "stages_completed"
        status["time_finished_ns"] = time.time_ns()
        save(stage_root / "status.json", status)
    except BaseException as exc:
        status.update({"status": "failed", "error": str(exc), "time_finished_ns": time.time_ns()})
        save(stage_root / "status.json", status)
        raise
    print("Coordinator completed", len(completed), "fio stages:", output)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as exc:
        print("COORDINATOR FAIL:", exc, file=sys.stderr)
        raise SystemExit(1)
