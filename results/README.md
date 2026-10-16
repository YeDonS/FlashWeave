# Archived experiment results / 实验结果归档

This bundle imports **37 unchanged files (6,874,591 bytes)** from the author's local `test p1` experiment collection. Imported on 2026-10-15; fio JSON timestamps record 2026-06-07. No corresponding machine/module manifest was found. Import time is not execution time.

本目录上传的是本地已有记录，未补造样本或重新运行设备测试。原始文件逐字节保留；SHA-256 用于核对上传后的文件完整性，不能单独证明历史运行环境与来源。

## Contents

| Path | Contents | Evidence scope |
| --- | --- | --- |
| [raw/fio/](raw/fio/) | 4 fio JSON outputs + 4 counter sidecars | Reported latency/bytes; counter snapshot timing unknown |
| [raw/sqlite/](raw/sqlite/) | 29 logs; original subdirectories retained | Application summaries, kernel tails and counters where present |
| [manifest.json](manifest.json) | Source-relative names, sizes and SHA-256 | Import integrity; unavailable build identity explicitly `null` |
| [derived/fio_summary.csv](derived/fio_summary.csv) | I/O volumes, latency, IOPS and counters | Recomputed from included files |
| [derived/sqlite_summary.csv](derived/sqlite_summary.csv) | One row per log, settings and parser outcomes | Diagnostic inventory; missing values stay empty |
| [derived/report.json](derived/report.json) | Full analysis, captured settings and diagnostics | Machine-readable evidence for each row |
| [derived/SUMMARY.md](derived/SUMMARY.md) | Tables linked to every raw run | Human-readable summary |
| [figures/](figures/) | Matplotlib SVG/PNG | Descriptive plots; no synthetic traces |

## Recompute

From the repository root:

```bash
python3 tools/reproduce_results.py --check
python3 tools/reproduce_results.py
```

The tool verifies every input hash/size, rejects unlisted files, requires measured fio jobs, rejects job errors, and checks completed bytes against configured `io_size`/`size`. A successful fio exit alone is insufficient.

Install [tools/requirements-results.txt](../tools/requirements-results.txt) and run `python3 tools/plot_results.py` for figures. Quantiles come from fio's reported completion latency. Request-level samples and histogram bins are absent; a CDF cannot be reconstructed from these files.

## Archive audit findings

1. **The filename is a ratio.** `rw10_1` means 10:1 bytes. These files request and complete 5 GiB reads / 0.5 GiB writes, not 10 GiB / 1 GiB. Initialization/prewarm volumes are 8/40 GiB.
2. **An older local summary belongs to different data.** `optimized_fio_sqlite_analysis/fio_summary.csv` reports 10/1 GiB and different latency values under the same basenames. Its original inputs were unavailable. That CSV and its plots are excluded; published tables use the raw files included here.
3. **Feature flags differ from mechanism activity.** V2 reports `compile_latency_v2_enabled=1`, but yields, delayed requeues and forced progress are zero. V3 also records zeros. V1 lacks those keys; missing values stay missing. Repromotion/rebalance are disabled across this fio comparison.
4. **The parser could mistake its own report for a warning.** Logs include an appended summary with `hung_task=0`. A broad substring check counted this as a hung-task message. The parser now ignores its own diagnostic lines; tests ensure real `blocked for more than ...` messages are still detected. After the fix, these archived files have no counted hung-task messages.
5. **Prewarm and operation semantics need more evidence.** SQLite outcomes are 16 `preheat_unproven`, 12 `yield_only(...)`, and one baseline. Some logs contain GC/read race retries and slow-path messages. Scan/random-row operations, threads and sample counts differ; these runs do not form a validated aggregate speedup estimate.

## Interpretation limits

- These are NVMeVirt emulation runs backed by host DRAM, not physical SLC/QLC measurements.
- One fio run per variant, `randrepeat=0`, no matched-seed record: no uncertainty estimates or causal attribution from this comparison alone.
- No historical commit/module hash connects the current source byte-for-byte to the archive. A sidecar mechanism name corroborates a family, not a build identity.
- No scoped fio kernel tails or foreground/post-drain counter captures. Zero fio errors cannot certify kernel activity or account for deferred maintenance debt.
- SQLite kernel capture and prewarm diagnostics are incomplete. A `baseline(...)` parser label classifies a mechanism; it does not certify experimental validity.
- Labels such as `zipf/19` and `zipf/28` are retained without assigning undocumented meaning. Distribution comes from each filename; `zipf/28` includes `normal` files.

Larger heat archives, drafts, presentations, dry-run manifests and summary-only later experiments are not mixed into this bundle. They lack a matching raw/build trail for the four-run comparison. Add further datasets with separate provenance.

## Next-run metadata

Record source commit, staged-input hashes, module SHA-256, kernel, CPU/NUMA/memory reservation, SSD configuration, compile flags, device identity, full command/configuration, explicit seed, exit code and scoped kernel log. Capture counters before/after measurement and after maintenance drains. Run matched seeds and report regressions as well as improvements.
