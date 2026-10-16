#!/usr/bin/env python3
"""Verify archived inputs and rebuild portable result tables (stdlib only)."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import analyze_latency_run as sqlite_parser
import fio_summarize_latency_mixed as fio_parser
from fio_validate_latency_mixed_json import size_to_bytes

CONTROL_COUNTERS = (
    "read_priority_yields", "read_priority_delayed_requeues",
    "read_priority_forced_progress_runs",
)
FIO_COUNTERS = CONTROL_COUNTERS + (
    "compile_latency_v2_enabled", "compile_qlc_hotcold_enabled",
    "compile_read_repromotion_enabled", "compile_qlc_rebalance_enabled",
    "read_requests", "host_read_nand_ops", "host_write_pages",
    "read_die_conflicts", "read_die_wait_ns", "bg_repromote_ops",
    "bg_qlc_rebalance_ops", "slc_to_qlc_migration_pages",
    "slc_gc_valid_copy_pages", "internal_write_pages_est",
    "mapped_tier_mismatch_pages", "host_read_tier_mismatch_pages",
)


def verify_manifest(root: Path) -> dict:
    manifest = json.loads((root / "results/manifest.json").read_text())
    listed = set()
    for entry in manifest["files"]:
        rel = Path(entry["path"])
        if rel.is_absolute() or ".." in rel.parts or rel.parts[:2] != ("results", "raw"):
            raise ValueError(f"Unsafe input path: {rel}")
        if rel.as_posix() in listed:
            raise ValueError(f"Duplicate input: {rel}")
        listed.add(rel.as_posix())
        payload = (root / rel).read_bytes()
        if len(payload) != entry["bytes"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise ValueError(f"Input checksum/size mismatch: {rel}")
    actual = {p.relative_to(root).as_posix() for p in (root / "results/raw").rglob("*") if p.is_file()}
    if actual != listed:
        raise ValueError(f"Raw files differ from manifest: {sorted(actual ^ listed)}")
    return manifest


def stats_from(path: Path) -> dict:
    if not path.exists():
        return {}
    stats = {}
    for line in path.read_text().splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            key, value = parts
            stats[key] = int(value) if re.fullmatch(r"-?\d+", value) else value
    return stats


def control_activity(stats: dict) -> str:
    values = [stats.get(key) for key in CONTROL_COUNTERS]
    if any(isinstance(value, int) and value > 0 for value in values):
        return "control_counters_nonzero"
    if any(value is None for value in values):
        return "unknown_missing_counters"
    return "no_control_activity_recorded"


def validate_fio(data: dict) -> tuple[list, dict]:
    jobs = data.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("No fio jobs; a dry-run manifest is not a measured run")
    if any(not isinstance(job, dict) or int(job.get("error", 0)) for job in jobs):
        raise ValueError("fio reported a job error")
    phases = {
        "init_write": ("init_write_", "write"),
        "init_prewarm": ("init_prewarm_", "read"),
        "measure_read": ("measure_reads", "read"),
        "measure_write": ("measure_writes", "write"),
    }
    volumes = {}
    for phase, (prefix, direction) in phases.items():
        selected = [j for j in jobs if str(j.get("jobname", "")).startswith(prefix)]
        if not selected:
            raise ValueError(f"Missing phase: {phase}")
        expected = 0
        actual = 0
        for job in selected:
            options = job.get("job options", {})
            size = options.get("io_size", options.get("size"))
            if size is None:
                raise ValueError(f"Missing configured I/O volume: {job['jobname']}")
            expected += size_to_bytes(str(size))
            actual += int(job.get(direction, {}).get("io_bytes", 0))
        if actual != expected or actual <= 0:
            raise ValueError(f"Incomplete {phase}: actual={actual}, configured={expected}")
        volumes[phase + "_bytes"] = actual
    for name in ("measure_reads", "measure_writes"):
        if sum(job.get("jobname") == name for job in jobs) != 1:
            raise ValueError(f"Expected one explicit {name} job")
    return jobs, volumes


def completion_ms(operation: dict, percentile: str) -> float | None:
    value = operation.get("clat_ns", {}).get("percentile", {}).get(percentile)
    return None if value is None else round(float(value) / 1e6, 6)


def fio_row(root: Path, rel: str, run_id: str) -> tuple[dict, dict]:
    path = root / rel
    data = fio_parser.load_fio_json(path)
    jobs, volumes = validate_fio(data)
    job_map = {job["jobname"]: job for job in jobs}
    read_job, write_job = job_map["measure_reads"], job_map["measure_writes"]
    read, write = read_job["read"], write_job["write"]
    stats_path = path.with_name("fio_test_phase_stats_" + path.stem + ".txt")
    stats = stats_from(stats_path)
    config_keys = ("ioengine", "direct", "thread", "group_reporting", "time_based", "norandommap", "randrepeat", "bs", "iodepth", "filename")
    job_keys = ("rw", "random_distribution", "offset", "size", "io_size", "iodepth", "numjobs")
    config = {"global": {k: data.get("global options", {}).get(k) for k in config_keys},
              "read": {k: read_job.get("job options", {}).get(k) for k in job_keys},
              "write": {k: write_job.get("job options", {}).get(k) for k in job_keys}}
    row = {"run_id": run_id, "variant": fio_parser.parse_run_name(path)["variant"],
           "fio_version": data.get("fio version"), "recorded_time": data.get("time"),
           "workload_signature": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16],
           "job_count": len(jobs), "fio_job_errors": 0, "volume_check": "pass", **volumes,
           "read_iops": round(float(read["iops"]), 6),
           "read_mean_ms": round(float(read["clat_ns"]["mean"]) / 1e6, 6),
           "read_p99_ms": completion_ms(read, "99.000000"),
           "read_p999_ms": completion_ms(read, "99.900000"),
           "read_p9999_ms": completion_ms(read, "99.990000"),
           "read_runtime_s": int(read["runtime"]) / 1000,
           "write_iops": round(float(write["iops"]), 6),
           "write_p99_ms": completion_ms(write, "99.000000"),
           "write_runtime_s": int(write["runtime"]) / 1000,
           "control_activity": control_activity(stats),
           "counter_snapshot_phase": "not_recorded",
           "raw_file": rel, "stats_file": stats_path.relative_to(root).as_posix() if stats_path.exists() else None}
    row.update({key: stats.get(key) for key in FIO_COUNTERS})
    details = {"run_id": run_id, "configuration": config, "all_sidecar_fields": stats,
               "latency_basis": "fio-reported clat_ns percentiles; histogram bins and per-I/O traces unavailable",
               "kernel_completion_status": "unknown: no scoped kernel log in this fio archive"}
    return row, details


def sqlite_row(root: Path, rel: str, run_id: str) -> tuple[dict, dict]:
    path = root / rel
    parsed = sqlite_parser.parse_log(path)
    name = path.stem
    match = re.search(r"init_(die_.+?)_(?:full_scan|random_row)_concurrent", name)
    stats = stats_from(path)
    summary = {}
    config = {}
    for line in path.read_text(errors="replace").splitlines():
        if "[sqlite_init] tag=" in line:
            summary.update(sqlite_parser.parse_kv_pairs(line))
        if "[sqlite_init] config " in line or "[sqlite_init] cold_extra_append " in line:
            config.update(sqlite_parser.parse_kv_pairs(line))
    latency = parsed["cold_read_latency"]
    signals = parsed["kernel_signals"]
    kernel_status = parsed["kernel_tail_status"] or {}
    mode = summary.get("cold_mode")
    row = {"run_id": run_id, "archive_group": path.parent.relative_to(root / "results/raw/sqlite").as_posix(),
           "variant": match.group(1) if match else str(parsed["tag"]),
           "access_dist": "normal" if "_normal_" in name else "zipf" if "_zipf_" in name else None,
           "threads_from_filename": int(re.search(r"_t(\d+)$", name).group(1)) if re.search(r"_t(\d+)$", name) else None,
           "cold_mode": mode, "cold_full_read_s": parsed["cold_full_read"],
           "cold_full_read_tp_mib_s": float(summary["cold_full_read_tp"].removesuffix("MB/s")) if "cold_full_read_tp" in summary else None,
           "latency_count": latency.get("count"),
           "kernel_tail_status": kernel_status.get("status", "not_recorded"),
           "hung_task_messages": signals.get("hung_task"),
           "no_free_entry_messages": signals.get("no_free_entry"),
           "parser_outcome": sqlite_parser.experiment_outcome(parsed),
           "raw_file": rel}
    for key in ("avg", "p50", "p95", "p99", "p999", "max"):
        row["read_" + key + "_ms"] = round(latency[key] * 1000, 6) if key in latency else None
    for key in CONTROL_COUNTERS + ("host_read_nand_ops", "read_die_conflicts", "slc_to_qlc_migration_pages"):
        row[key] = stats.get(key)
    for key in ("tables", "total_rows", "logical_row_bytes", "read_ops_per_event", "strict_cold_per_select", "direct_io", "target_bytes", "read_ratio"):
        row["config_" + key] = config.get(key)
    details = {"run_id": run_id, "configuration": config, "summary": summary,
               "diagnostics": sqlite_parser.verdict(parsed), "kernel_signals": signals,
               "kernel_tail_capture": kernel_status,
               "latency_basis": "SQLite-reported summary; operation semantics differ between full scans and random-row runs"}
    return row, details


def csv_text(rows: list[dict]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def display(value, digits=3) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}" if isinstance(value, float) else str(value)


def outputs(root: Path) -> dict[str, str]:
    manifest = verify_manifest(root)
    fio, sql, details = [], [], []
    files = sorted(manifest["files"], key=lambda entry: entry["path"])
    for entry in files:
        if entry["kind"] == "fio_json":
            row, detail = fio_row(root, entry["path"], f"F{len(fio) + 1:02d}")
            fio.append(row); details.append(detail)
        elif entry["kind"] == "sqlite_log":
            row, detail = sqlite_row(root, entry["path"], f"S{len(sql) + 1:02d}")
            sql.append(row); details.append(detail)
    if not fio or not sql:
        raise ValueError("Expected archived fio and SQLite runs")
    counts = Counter(row["parser_outcome"].split("(")[0] for row in sql)
    report = {"dataset_id": manifest["dataset_id"], "raw_file_count": len(files),
              "raw_bytes": sum(entry["bytes"] for entry in files), "fio_runs": fio,
              "sqlite_runs": sql, "sqlite_parser_outcomes": dict(sorted(counts.items())),
              "run_details": details}
    md = ["# Recomputed archive tables", "", "Generated by `python3 tools/reproduce_results.py`. Raw input hashes are checked before analysis.", "",
          "## fio: recorded completion latency", "",
          "One run per variant. These are NVMeVirt emulation observations, not repeated-seed estimates or physical SSD measurements.", "",
          "| Run | Variant | Read IOPS | Read p99 (ms) | Read p99.9 (ms) | Read p99.99 (ms) |", "| --- | --- | ---: | ---: | ---: | ---: |"]
    for row in fio:
        md.append(f"| [{row['run_id']}](../{row['raw_file'].removeprefix('results/')}) | `{row['variant']}` | {display(row['read_iops'])} | {display(row['read_p99_ms'])} | {display(row['read_p999_ms'])} | {display(row['read_p9999_ms'])} |")
    md += ["", "All four runs request and complete 8 GiB initialization writes, 40 GiB interleaved prewarm reads, 5 GiB measured reads and 0.5 GiB measured writes. `rw10_1` describes the byte ratio, not 10 GiB of reads.", "",
           "All measured jobs have zero fio errors. The JSON does not include latency histogram bins or per-I/O traces: percentiles are extracted from fio, not independently estimated.", "",
           "## fio: mechanism evidence", "",
           "| Run | Read-priority yields | Delayed requeues | Forced progress | Hot/cold flag | Repromotion flag |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for row in fio:
        md.append("| " + " | ".join(display(row[key]) for key in ("run_id", "read_priority_yields", "read_priority_delayed_requeues", "read_priority_forced_progress_runs", "compile_qlc_hotcold_enabled", "compile_read_repromotion_enabled")) + " |")
    md += ["", "A compiled feature flag is not proof that the execution path ran. Sidecar capture/reset timing is absent; these counters must not be labeled foreground/post-drain deltas. Full counter values are in `report.json`.", "",
           "## SQLite: diagnostic inventory", "",
           "Each row is a separate archived file. Folder names are retained as labels; they do not establish matched seeds or byte-identical workloads. Full-scan times and random-row latency are not comparable units of work.", "",
           "| Run | Archive group | Variant | Mode | Time (s) | Latency samples | Parser outcome |", "| --- | --- | --- | --- | ---: | ---: | --- |"]
    for row in sql:
        md.append(f"| [{row['run_id']}](../{row['raw_file'].removeprefix('results/')}) | `{row['archive_group']}` | `{row['variant']}` | {row['cold_mode'] or '—'} | {display(row['cold_full_read_s'])} | {display(row['latency_count'])} | `{row['parser_outcome']}` |")
    md += ["", "Parser outcome counts: " + ", ".join(f"`{key}`={count}" for key, count in sorted(counts.items())) + ".", "",
           "Kernel warnings are reported as observed log evidence. Incomplete capture may also contain unrelated activity; the archive lacks sufficient manifests to resolve that attribution. Rows with I/O warnings or unproven prewarm are retained for diagnosis and excluded from headline performance claims.", "",
           "See [results documentation](../README.md) for provenance and limitations. Empty CSV fields mean unavailable, not zero.", ""]
    return {"fio_summary.csv": csv_text(fio), "sqlite_summary.csv": csv_text(sql),
            "report.json": json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            "SUMMARY.md": "\n".join(md)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify inputs and compare committed tables without modifying files")
    parser.add_argument("--output", type=Path, default=ROOT / "results/derived")
    args = parser.parse_args()
    try:
        generated = outputs(ROOT)
        if args.check:
            stale = [name for name, text in generated.items() if not (args.output / name).exists() or (args.output / name).read_text() != text]
            if stale:
                raise ValueError("Stale/missing derived files: " + ", ".join(stale))
        else:
            args.output.mkdir(parents=True, exist_ok=True)
            for name, text in generated.items():
                (args.output / name).write_text(text)
        raw_count = json.loads(generated["report.json"])["raw_file_count"]
        print(f"PASS: verified {raw_count} raw inputs and {'checked' if args.check else 'rebuilt'} {len(generated)} derived files")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
