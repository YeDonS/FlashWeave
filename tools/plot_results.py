#!/usr/bin/env python3
"""Plot verified archived fio metrics; no extrapolation or synthetic samples."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reproduce_results import ROOT, outputs

LABELS = ("V1 plain", "V1 hot/cold", "V2 hot/cold", "V3 hot/cold")
COLORS = ("#64748b", "#0891b2", "#2563eb", "#7c3aed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "results/figures")
    args = parser.parse_args()
    report = json.loads(outputs(ROOT)["report.json"])
    rows = report["fio_runs"]
    if len(rows) != 4:
        raise ValueError("Plot labels are defined for the four archived fio runs")
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "svg.hashsalt": "dac25-local-latency-archive",
                         "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(1, 3, figsize=(12.8, 4.6))
    for axis, (field, title) in zip(axes, (("read_p99_ms", "Read p99"), ("read_p999_ms", "Read p99.9"), ("read_p9999_ms", "Read p99.99"))):
        values = [row[field] for row in rows]
        bars = axis.bar(LABELS, values, color=COLORS, width=0.65)
        axis.bar_label(bars, labels=[f"{value:.2f}" for value in values], padding=4, fontsize=9)
        axis.set(title=title, ylabel="Completion latency (ms)", ylim=(0, max(values) * 1.2))
        axis.tick_params(axis="x", labelrotation=25)
        axis.set_axisbelow(True)
        axis.grid(axis="y", alpha=0.18)
    fig.suptitle("Archived fio runs: Zipf 0.75, 4 KiB, iodepth 32", fontsize=14, x=0.51)
    fig.text(0.5, 0.015, "NVMeVirt emulation • 5 GiB reads / 0.5 GiB writes • one run per variant • no confidence intervals", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, 0.055, 1, 0.95))
    args.output.mkdir(parents=True, exist_ok=True)
    for extension in ("svg", "png"):
        fig.savefig(args.output / f"fio_read_latency.{extension}", dpi=150,
                    metadata={"Date": None, "Creator": "Dac25_ae tools/plot_results.py"} if extension == "svg" else {"Software": "Dac25_ae tools/plot_results.py"})
    plt.close(fig)
    print("PASS: plotted recorded fio percentiles (SVG + PNG)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
