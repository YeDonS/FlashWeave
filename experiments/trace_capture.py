#!/usr/bin/env python3
"""Explicit diagnostic-only tracefs capture in a private per-run instance.

start --out FRESH_DIR synchronously returns after a detached reader is ready.
stop --out EXISTING_DIR stops only that session, archives evidence and waits.
The module's three FlashWeave events must exist before start and remain loaded
until stop returns. No global tracer, mount, kernel module or workload is changed.
All live capture commands require Linux root. Parser helpers are pure Python.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import select
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

EVENTS = ("nand_schedule", "resource_schedule", "worker_decision")
GROUP = "flashweave"
LOSS_KEYS = ("overrun", "commit_overrun", "dropped_events")
STATS_KEYS = ("entries", "read_events", *LOSS_KEYS)


def atomic_json(path, data):
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    temp.replace(path)


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def digest_file(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def require_root():
    if platform.system() != "Linux" or os.geteuid() != 0:
        raise RuntimeError("live capture requires Linux root; parser/fixtures need neither")


def parse_cpu_stats(raw):
    """Parse tracefs per_cpu/cpu*/stats without treating absent counters as zero."""
    parsed = {}
    for line in raw.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
        value = value.strip()
        if re.fullmatch(r"[0-9]+", value):
            parsed[key] = int(value)
    missing = [key for key in STATS_KEYS if key not in parsed]
    return {"values": parsed, "missing_required": missing}


def loss_summary(before, after):
    problems, deltas, entries_after, read_events = [], {}, {}, {}
    if not before or set(before) != set(after):
        problems.append("per-CPU inventory missing or changed; loss cannot be verified")
    for cpu in sorted(set(before) | set(after)):
        if cpu not in before or cpu not in after:
            continue
        old, new = before[cpu], after[cpu]
        if old["missing_required"] or new["missing_required"]:
            problems.append(cpu + ": missing required stats fields")
        entries_after[cpu] = new["values"].get("entries")
        if entries_after[cpu] is not None and entries_after[cpu] != 0:
            problems.append(cpu + ": entries remain after stopped-stream drain")
        read_events[cpu] = {"before": old["values"].get("read_events"),
                            "after": new["values"].get("read_events")}
        delta = {}
        for key in LOSS_KEYS:
            if key not in old["values"] or key not in new["values"]:
                continue
            value = new["values"][key] - old["values"][key]
            delta[key] = value
            if value < 0:
                problems.append(cpu + ": counter reset/reload: " + key)
            elif value:
                problems.append(cpu + ": loss counter increased: " + key)
        deltas[cpu] = delta
    return {"verified_no_loss": not problems, "per_cpu_deltas": deltas,
            "per_cpu_entries_after": entries_after, "per_cpu_read_events": read_events,
            "read_events_scope": "raw counter observations; not assumed equal to parsed event count",
            "problems": problems}


def validate_event_identity(initial, current):
    """Event IDs/layouts changing during a run imply a module lifecycle break."""
    problems = []
    for name in EVENTS:
        if name not in initial or name not in current:
            problems.append(name + ": event disappeared or was never registered")
        elif initial[name] != current[name]:
            problems.append(name + ": event ID or format changed (possible module reload)")
    return problems


def find_tracefs(explicit=None):
    candidates = [Path(explicit)] if explicit else [Path("/sys/kernel/tracing"), Path("/sys/kernel/debug/tracing")]
    for root in candidates:
        if (root / "instances").is_dir() and (root / "events").is_dir():
            return root.resolve()
    raise RuntimeError("mounted tracefs with instances is unavailable; no automatic mount is attempted")


def event_identity(root):
    result = {}
    for name in EVENTS:
        folder = root / "events" / GROUP / name
        if not (folder / "format").is_file() or not (folder / "id").is_file():
            continue
        raw = (folder / "format").read_bytes()
        result[name] = {"id": int((folder / "id").read_text().strip()), "format_sha256": digest_bytes(raw)}
    return result


def collect_stats(instance, out, label):
    destination = out / ("per_cpu_" + label)
    destination.mkdir(exist_ok=False)
    result = {}
    for path in sorted((instance / "per_cpu").glob("cpu*/stats")):
        raw = path.read_text()
        (destination / (path.parent.name + ".txt")).write_text(raw)
        result[path.parent.name] = parse_cpu_stats(raw)
    atomic_json(out / ("per_cpu_" + label + ".json"), result)
    return result


def read_json(path):
    return json.loads(Path(path).read_text())


def checked_control(out):
    control = read_json(out / "control.json")
    token = control.get("token", "")
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise RuntimeError("invalid session token")
    if control.get("instance_name") != "fw_diag_" + token[:16]:
        raise RuntimeError("invalid private instance identity")
    return control


def start(args):
    require_root()
    requested = Path(args.out).expanduser()
    if requested.exists() or requested.is_symlink():
        raise RuntimeError("--out must be a fresh nonexistent directory")
    out = requested.resolve()
    out.mkdir(parents=True, mode=0o700, exist_ok=False)
    token = uuid.uuid4().hex
    command = [sys.executable, str(Path(__file__).resolve()), "_serve", "--out", str(out),
               "--token", token, "--buffer-kb", str(args.buffer_kb),
               "--max-seconds", str(args.max_seconds), "--max-bytes", str(args.max_bytes)]
    if args.tracefs:
        command += ["--tracefs", args.tracefs]
    atomic_json(out / "launch.json", {"argv": command, "time_ns": time.time_ns(), "scope": "diagnostic_only"})
    with (out / "controller.log").open("wb") as stream:
        process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=True, close_fds=True)
    deadline = time.monotonic() + args.ready_timeout
    while time.monotonic() < deadline:
        if (out / "ready.json").is_file():
            ready = read_json(out / "ready.json")
            print(json.dumps(ready, sort_keys=True))
            if ready.get("status") != "ready":
                return 2
            return 0
        if process.poll() is not None:
            raise RuntimeError("capture service exited before ready; preserved controller.log/result.json")
        time.sleep(0.05)
    # Tokenized file request cannot signal or mutate an unrelated process/session.
    atomic_json(out / "stop.request.json", {"token": token, "reason": "startup_timeout"})
    raise RuntimeError("capture startup timed out; stop requested and failure evidence retained")


def stop(args):
    require_root()
    out = Path(args.out).expanduser().resolve()
    control = checked_control(out)
    if not (out / "result.json").is_file():
        atomic_json(out / "stop.request.json", {"token": control["token"], "reason": "requested_stop"})
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if (out / "result.json").is_file():
            result = read_json(out / "result.json")
            print(json.dumps({"status": result["status"], "out": str(out),
                              "incomplete": result["incomplete"], "problems": result["problems"]}, sort_keys=True))
            return 0 if result["status"] == "captured" else 2
        time.sleep(0.05)
    raise RuntimeError("capture stop timed out; session evidence preserved, do not unload module yet")


def serve(args):
    require_root()
    out = Path(args.out).resolve()
    token = args.token
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise RuntimeError("invalid internal session token")
    instance_name = "fw_diag_" + token[:16]
    result = {"schema": "flashweave-trace-capture-v1", "status": "capture_failed", "incomplete": True,
              "diagnostic_only": True, "problems": [], "instance_name": instance_name,
              "started_ns": time.time_ns(), "bytes_written": 0,
              "global_tracer_modified": False, "max_bytes": args.max_bytes,
              "disk_cost_note": "streamed diagnostic text can occupy several GiB; formal performance tracing must remain off",
              "scope": "host decision/reservation trace; not observed hardware completion or full drain"}
    instance, pipe_fd, before = None, None, {}
    instance_owned = False
    result["private_instance_created"] = False
    signal_reason = []

    def interrupted(number, _frame):
        signal_reason.append("signal_" + str(number))

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    atomic_json(out / "control.json", {"token": token, "pid": os.getpid(), "instance_name": instance_name})
    try:
        root = find_tracefs(args.tracefs)
        result["tracefs_root"] = str(root)
        initial = event_identity(root)
        if set(initial) != set(EVENTS):
            raise RuntimeError("all three module trace events must exist before start; start after module load")
        result["event_identity_start"] = initial
        instance = root / "instances" / instance_name
        instance.mkdir(exist_ok=False)
        instance_owned = True
        result["private_instance_created"] = True
        result["instance_path"] = str(instance)
        (instance / "tracing_on").write_text("0\n")
        (instance / "events" / "enable").write_text("0\n")
        (instance / "current_tracer").write_text("nop\n")
        (instance / "buffer_size_kb").write_text(str(args.buffer_kb) + "\n")
        result["buffer_size_kb_readback"] = (instance / "buffer_size_kb").read_text().strip()
        available = (instance / "trace_clock").read_text()
        if "mono" not in available.replace("[", "").replace("]", "").split():
            raise RuntimeError("private instance does not support required mono clock")
        (instance / "trace_clock").write_text("mono\n")
        result["trace_clock_readback"] = (instance / "trace_clock").read_text().strip()
        if "[mono]" not in result["trace_clock_readback"]:
            raise RuntimeError("trace clock readback did not select mono")
        (instance / "trace").write_text("")
        formats = out / "event_formats"
        formats.mkdir()
        for name in EVENTS:
            folder = instance / "events" / GROUP / name
            (formats / (name + ".format.txt")).write_bytes((folder / "format").read_bytes())
            (folder / "enable").write_text("1\n")
            if (folder / "enable").read_text().strip() != "1":
                raise RuntimeError("event enable readback failed: " + name)
        before = collect_stats(instance, out, "before")
        if not before or any(data["missing_required"] for data in before.values()):
            raise RuntimeError("required per-CPU loss counters are unavailable")
        pipe_fd = os.open(instance / "trace_pipe", os.O_RDONLY | os.O_NONBLOCK)
        (instance / "tracing_on").write_text("1\n")
        if (instance / "tracing_on").read_text().strip() != "1":
            raise RuntimeError("private tracing_on readback failed")
        result["enabled_events"] = [GROUP + ":" + name for name in EVENTS]
        atomic_json(out / "ready.json", {"status": "ready", "pid": os.getpid(),
                    "out": str(out), "instance_name": instance_name, "diagnostic_only": True})
        started, next_identity_check = time.monotonic(), time.monotonic()
        with (out / "trace.txt").open("wb") as stream:
            stopping = False
            loss_tail = b""

            def store(chunk):
                nonlocal loss_tail
                sample = loss_tail + chunk
                if re.search(rb"\bLOST\s+(?:[0-9]+\s+)?EVENTS\b", sample, re.I):
                    result["problems"].append("trace_pipe_reports_lost_events")
                loss_tail = sample[-128:]
                stream.write(chunk)
                result["bytes_written"] += len(chunk)

            while not stopping:
                if signal_reason:
                    result["problems"].extend(signal_reason)
                    break
                request = out / "stop.request.json"
                if request.is_file():
                    data = read_json(request)
                    if data.get("token") != token:
                        raise RuntimeError("stop request has wrong session token")
                    result["stop_reason"] = data.get("reason", "requested_stop")
                    break
                if time.monotonic() - started >= args.max_seconds:
                    result["problems"].append("max_seconds_reached")
                    break
                if time.monotonic() >= next_identity_check:
                    problems = validate_event_identity(initial, event_identity(root))
                    if problems:
                        result["problems"].extend(problems)
                        break
                    next_identity_check = time.monotonic() + 0.5
                readable, _, _ = select.select([pipe_fd], [], [], 0.05)
                if readable:
                    try:
                        chunk = os.read(pipe_fd, 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        raise RuntimeError("trace_pipe unexpectedly closed")
                    if result["bytes_written"] + len(chunk) > args.max_bytes:
                        result["problems"].append("max_bytes_reached; stream is incomplete")
                        result["unwritten_chunk_bytes"] = len(chunk)
                        break
                    store(chunk)
            # Turn off production before draining/reading the remainder or stats.
            (instance / "tracing_on").write_text("0\n")
            result["event_identity_stop"] = event_identity(root)
            result["problems"].extend(validate_event_identity(initial, result["event_identity_stop"]))
            if not any("max_bytes" in problem for problem in result["problems"]):
                empty_polls = 0
                while empty_polls < 3:
                    try:
                        chunk = os.read(pipe_fd, 65536)
                    except BlockingIOError:
                        chunk = b""
                    if not chunk:
                        empty_polls += 1
                        time.sleep(0.02)
                        continue
                    empty_polls = 0
                    if result["bytes_written"] + len(chunk) > args.max_bytes:
                        result["problems"].append("max_bytes_reached_while_draining; stream is incomplete")
                        break
                    store(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        after = collect_stats(instance, out, "after")
        result["loss"] = loss_summary(before, after)
        result["problems"].extend(result["loss"]["problems"])
        # Non-consuming remainder snapshot and header: authoritative event stream
        # is trace.txt. Never concatenate this snapshot and double-count events.
        (out / "trace.remaining.txt").write_bytes((instance / "trace").read_bytes())
        if result["bytes_written"] == 0:
            result["problems"].append("empty_event_stream")
    except (OSError, ValueError, RuntimeError) as exc:
        result["problems"].append(type(exc).__name__ + ": " + str(exc))
    finally:
        if instance_owned and instance is not None and instance.is_dir():
            try:
                (instance / "tracing_on").write_text("0\n")
            except OSError as exc:
                result["problems"].append("could_not_stop_private_tracing: " + str(exc))
            for name in EVENTS:
                try:
                    path = instance / "events" / GROUP / name / "enable"
                    if path.exists():
                        path.write_text("0\n")
                except OSError as exc:
                    result["problems"].append("could_not_disable_private_event: " + name + ": " + str(exc))
            if pipe_fd is not None:
                os.close(pipe_fd)
                pipe_fd = None
            try:
                instance.rmdir()  # tracefs removes this instance, never another.
                result["private_instance_removed"] = True
            except OSError as exc:
                result["problems"].append("could_not_remove_private_instance: " + str(exc))
        if pipe_fd is not None:
            os.close(pipe_fd)
        result["problems"] = list(dict.fromkeys(result["problems"]))
        result["finished_ns"] = time.time_ns()
        result["incomplete"] = bool(result["problems"])
        result["status"] = "captured" if not result["incomplete"] else "capture_failed"
        result["artifacts_sha256"] = {str(path.relative_to(out)): digest_file(path)
             for path in sorted(out.rglob("*")) if path.is_file() and path.name not in
             ("result.json", "result.json.tmp", "controller.log")}
        if not (out / "ready.json").exists():
            atomic_json(out / "ready.json", {"status": "failed", "problems": result["problems"], "out": str(out)})
        atomic_json(out / "result.json", result)
    return 0 if result["status"] == "captured" else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    start_parser = commands.add_parser("start")
    start_parser.add_argument("--out", required=True)
    start_parser.add_argument("--tracefs")
    start_parser.add_argument("--buffer-kb", type=int, default=1024, help="per-CPU buffer size")
    start_parser.add_argument("--max-bytes", type=int, default=8 * 1024**3,
                              help="streamed text cap; default 8 GiB, reaching it fails the capture")
    start_parser.add_argument("--max-seconds", type=int, default=3600)
    start_parser.add_argument("--ready-timeout", type=int, default=15)
    start_parser.set_defaults(func=start)
    stop_parser = commands.add_parser("stop")
    stop_parser.add_argument("--out", required=True)
    stop_parser.add_argument("--timeout", type=int, default=300,
                             help="allows streaming-evidence hashing on slower disks")
    stop_parser.set_defaults(func=stop)
    service = commands.add_parser("_serve", help=argparse.SUPPRESS)
    service.add_argument("--out", required=True)
    service.add_argument("--token", required=True)
    service.add_argument("--tracefs")
    service.add_argument("--buffer-kb", type=int, required=True)
    service.add_argument("--max-bytes", type=int, required=True)
    service.add_argument("--max-seconds", type=int, required=True)
    service.set_defaults(func=serve)
    args = parser.parse_args()
    for name in ("buffer_kb", "max_bytes", "max_seconds", "ready_timeout", "timeout"):
        if hasattr(args, name) and getattr(args, name) <= 0:
            parser.error(name + " must be positive")
    try:
        return args.func(args)
    except (OSError, ValueError, RuntimeError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
