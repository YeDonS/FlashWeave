#!/usr/bin/env python3
"""Validate a complete matched 20-run batch and emit descriptive paired tables.

No benchmark is run here. Missing metrics remain NA. Seed/run is the unit of
replication. Ratios of run-level percentiles are not a pooled I/O percentile.
"""
from __future__ import annotations
import argparse, contextlib, csv, io, json, math, statistics
from pathlib import Path
from types import SimpleNamespace
import protocol

SEEDS = (101, 202, 303, 404, 505)
CELLS = ("A", "B", "C", "D")
PAIRS = (("B", "A"), ("C", "A"), ("D", "A"), ("D", "B"), ("D", "C"))
METRICS = ("read_mean_ms", "read_p99_ms", "read_p99_9_ms", "read_p99_99_ms",
           "read_iops", "write_mean_ms", "write_p99_ms")
COUNTERS = ("read_priority_yields", "read_priority_delayed_requeues",
            "read_priority_forced_progress_runs", "slc_to_qlc_migration_pages",
            "slc_gc_valid_copy_pages", "internal_write_pages_est")


def number(value):
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError("non-finite/negative metric")
    return value


def percentile(job, key):
    values = job.get("clat_ns", {}).get("percentile", {})
    for label, value in values.items():
        if float(label) == key:
            value = number(value)
            return None if value is None else value / 1_000_000
    return None


def extract_metrics(document):
    by_name = {}
    for job in document["jobs"]:
        if job["jobname"] in by_name:
            raise ValueError("duplicate job name; cloned jobs unsupported")
        by_name[job["jobname"]] = job
    read = by_name["measure_reads"]["read"]
    write = by_name["measure_writes"]["write"]
    mean = lambda job: number(job.get("clat_ns", {}).get("mean"))
    rmean, wmean = mean(read), mean(write)
    return {"read_mean_ms": None if rmean is None else rmean / 1_000_000,
            "read_p99_ms": percentile(read, 99),
            "read_p99_9_ms": percentile(read, 99.9),
            "read_p99_99_ms": percentile(read, 99.99),
            "read_iops": number(read.get("iops")),
            "write_mean_ms": None if wmean is None else wmean / 1_000_000,
            "write_p99_ms": percentile(write, 99),
            "read_runtime_ms": number(read.get("runtime")),
            "write_runtime_ms": number(write.get("runtime")),
            "read_total_ios": read.get("total_ios"),
            "write_total_ios": write.get("total_ios")}


def require_matrix(records):
    index = {}
    for row in records:
        key = (row["seed"], row["cell"])
        if key in index:
            raise ValueError("duplicate seed/cell: " + repr(key))
        index[key] = row
    wanted = {(seed, cell) for seed in SEEDS for cell in CELLS}
    if set(index) != wanted:
        raise ValueError("complete formal matrix required; missing=" +
                         repr(sorted(wanted - set(index))) + "; unexpected=" +
                         repr(sorted(set(index) - wanted)))
    return index


def paired_tables(records):
    index = require_matrix(records)
    paired = []
    for seed in SEEDS:
        for metric in METRICS:
            for numerator, denominator in PAIRS:
                a = index[(seed, numerator)].get(metric)
                b = index[(seed, denominator)].get(metric)
                ratio = None if a is None or b is None or b <= 0 else a / b
                paired.append({"seed": seed, "metric": metric,
                               "comparison": numerator + "/" + denominator,
                               "ratio": ratio})
            vals = [index[(seed, cell)].get(metric) for cell in CELLS]
            interaction = None
            if all(value is not None and value > 0 for value in vals):
                a, b, c, d = vals
                interaction = (d / c) / (b / a)
            paired.append({"seed": seed, "metric": metric,
                           "comparison": "(D/C)/(B/A)", "ratio": interaction})
    summary = []
    for metric in METRICS:
        for comparison in [a + "/" + b for a, b in PAIRS] + ["(D/C)/(B/A)"]:
            valid = [row["ratio"] for row in paired if row["metric"] == metric and
                     row["comparison"] == comparison and row["ratio"] is not None and row["ratio"] > 0]
            summary.append({"metric": metric, "comparison": comparison,
                            "paired_seed_count": len(valid),
                            "geometric_mean_ratio": math.exp(statistics.mean(map(math.log, valid))) if valid else None,
                            "median_ratio": statistics.median(valid) if valid else None,
                            "min_ratio": min(valid) if valid else None,
                            "max_ratio": max(valid) if valid else None,
                            "scope": "descriptive run-level paired ratios; no significance claim"})
    return paired, summary


def write_csv(path, rows):
    with path.open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({key: "NA" if value is None else value for key, value in row.items()}
                         for row in rows)


def load_record(run):
    # Reuse the run gate; a zero exit or a summary file alone is insufficient.
    with contextlib.redirect_stdout(io.StringIO()):
        protocol.check(SimpleNamespace(run=str(run)))
    manifest = json.loads((run / "manifest.json").read_text())
    if manifest.get("profile") != "formal":
        raise ValueError("pilot/pressure cannot enter formal batch")
    if manifest.get("trace_mode") != "off":
        raise ValueError("diagnostic trace runs cannot enter the performance batch")
    if not manifest.get("source_commit") or manifest.get("source_dirty"):
        raise ValueError("clean committed source identity is required")
    seed, cell = manifest["seed"], manifest["cell"]
    raw = run / "raw"
    files = list(raw.rglob("fio_mixed_*.json"))
    if len(files) != 1:
        raise ValueError("one merged fio JSON+ required")
    record = {"seed": seed, "cell": cell, "variant": manifest["variant"], "run": str(run),
              **extract_metrics(json.loads(files[0].read_text()))}
    baselines = list(raw.rglob("*.baseline/snapshot.json"))
    if len(baselines) != 1:
        raise ValueError("one baseline required")
    baseline = json.loads(baselines[0].read_text())
    prefix = files[0].stem
    # F/Q raw sidecars retain the actual active-phase captures. The final
    # helper snapshot may be after phase exit and is not substituted for F/Q.
    fg_path = files[0].parent / ("fio_test_phase_stats_aggregate_" + prefix + ".txt")
    q_path = files[0].parent / ("fio_test_phase_stats_aggregate_" + prefix + "_post_drain.txt")
    def counts(path):
        result = {}
        for line in path.read_text().splitlines():
            words = line.split()
            if len(words) == 2:
                try: result[words[0]] = int(words[1])
                except ValueError: pass
        return result
    foreground, stationary = counts(fg_path), counts(q_path)
    before = baseline["stats"]
    for counter in COUNTERS:
        for name, end, start in (("F_minus_B", foreground, before),
                                  ("Q_minus_B", stationary, before),
                                  ("Q_minus_F", stationary, foreground)):
            delta = None
            if counter in end and counter in start:
                delta = end[counter] - start[counter]
                if delta < 0:
                    raise ValueError("counter reset/wrap across capture boundary: " + counter)
            record[counter + "_" + name] = delta
    binding = {key: manifest.get(key) for key in
               ("source_commit", "protocol_version", "workspace_manifest_sha256", "kernel", "host_cpus", "build_flags", "fio_binary",
                "machine_argument_validation")}
    selected = ("FIO_RW_RATIOS", "FIO_REGION_SIZE", "FIO_INIT_WRITE_BYTES", "FIO_INIT_CHUNK_SIZE",
                "FIO_INIT_PREWARM_BYTES_PER_CHUNK", "FIO_MEASURE_READ_BYTES", "FIO_MEASURE_WRITE_BYTES",
                "FIO_MEASURE_WRITE_OFFSET", "FIO_MEASURE_WRITE_REGION_SIZE", "FIO_MEASURE_READ_JOBS",
                "FIO_MEASURE_WRITE_JOBS", "FIO_MEASURE_READ_IODEPTH", "FIO_MEASURE_WRITE_IODEPTH",
                "FIO_ZIPF_ALPHA", "FIO_GC_NAND_TIMING", "FIO_TEST_PHASE_RECENT_WRITE_GUARD",
                "FIO_TEST_PHASE_GUARD_READ_REQS")
    binding["workload"] = {key: manifest["environment"].get(key) for key in selected}
    # Device geometry and runtime controls are required by phase_hook; compare
    # their actual readbacks across all four cells and all five seed blocks.
    binding["geometry"] = baseline.get("geometry")
    binding["controls"] = baseline.get("controls")
    binding["latency_profile"] = baseline.get("latency_profile")
    return record, binding


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--out", required=True, help="fresh analysis directory outside raw runs")
    args = parser.parse_args()
    batch, out = Path(args.batch).resolve(), Path(args.out).resolve()
    if out.exists():
        parser.error("output directory exists")
    records, reference = [], None
    for manifest in sorted(batch.glob("*/manifest.json")):
        row, binding = load_record(manifest.parent)
        if reference is None:
            reference = binding
        elif binding != reference:
            raise ValueError("non-treatment environment/config differs: " + str(manifest.parent))
        records.append(row)
    paired, summaries = paired_tables(records)
    if any(row.get("read_p99_9_ms") is None for row in records):
        raise ValueError("preregistered primary read P99.9 is missing")
    # Create deliverables only after validating every run and the complete matrix.
    out.mkdir(parents=True)
    records.sort(key=lambda row: (row["seed"], row["cell"]))
    write_csv(out / "run_summary.csv", records)
    write_csv(out / "paired_ratios.csv", paired)
    write_csv(out / "paired_summary.csv", summaries)
    status = {"status": "validated_descriptive_batch", "runs": len(records), "seeds": list(SEEDS),
              "primary_metric": "read_p99_9_ms",
              "scope": "V1/V3 scheduler-family comparison; seed/run is the replicate",
              "missing_data_marks": {"full_model_drain": "DATA REQUIRED",
                                     "hardware_NAND_nonpreemption": "DATA REQUIRED",
                                     "event_causal_validation": "DATA REQUIRED: separate diag trace",
                                     "dynamic_heat_and_relocation": "DATA REQUIRED",
                                     "SQLite_matched_application_experiments": "DATA REQUIRED"},
              "limits": ["Five seeds support descriptive uncertainty, not automatic significance.",
                         "Q is statistical stationarity; complete maintenance debt retirement remains unverified.",
                         "A ratio summary of run-level P99s is not a pooled completion-time percentile.",
                         "Review scoped kernel diagnostics before accepting experimental conclusions."]}
    (out / "status.json").write_text(json.dumps(status, indent=2) + "\n")
    print("Validated 20 matched runs. Descriptive tables:", out)


if __name__ == "__main__":
    main()

