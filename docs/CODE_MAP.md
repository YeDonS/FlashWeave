# Code map / 代码分类

The repository contains an inherited module plus newer root-level overlays. This index classifies those files without breaking existing include paths or experiment-script filenames. [prepare_workspace.py](../tools/prepare_workspace.py) creates a complete `nvmevirt_DA/` + `evaluation/` workspace for builds.

## 1. NVMe request processing / 请求处理

| Files | Responsibility |
| --- | --- |
| Root `main.c`, `admin.c`, `io.c`, `nvmev.h` | Newer module initialization, administration and I/O request processing |
| `nvmevirt_DA/pci.c`, `dma.c`, `nvme.h`, `pqueue/` | Supporting code supplied by the complete module snapshot |
| [nvmevirt_DA/Makefile](../nvmevirt_DA/Makefile) | Kbuild entry point; staged copy receives `NVMEV_EXTRA_CFLAGS` forwarding |

## 2. Flash translation and placement / FTL 与页面放置

| Family | Files | Use |
| --- | --- | --- |
| Integrated | [conv_ftl.c](../conv_ftl.c), [conv_ftl.h](../conv_ftl.h) | Chain aggregation, QLC hot/cold, repromotion and rebalance with compile-time ablations |
| Baseline | `conv_ftl_baseline*.c`, `conv_ftl_base2.c`, `conv_ftl_base3.c` | Historical baseline implementations and mapping-cache variants |
| Legacy ablations | `conv_ftl_no1*.c` through `conv_ftl_no4.c`, `conv_ftl_all_superblock.c` | Earlier feature combinations and superblock accounting |
| Maintenance schedulers | `conv_ftl_latency1_superblock.c`, `conv_ftl_latency2_superblock.c`, `conv_ftl_latency3_superblock.c` | V1 maintenance, V2 per-die idle/demand scheduling, V3 foreground-read-priority workers |
| Scheduler wrappers | `conv_ftl_latency[123]_{norp,qlc_hotcold,qlc_all,qlc_all_norp}_superblock.c` | Explicit feature macros followed by an include of a scheduler core |
| Unmapped historical files | `conv_ftl_nd.c`, `conv_ftl_latency_superblock.c`, `evaluation/conv_ftl.c` | Retained snapshots not selected directly by current `ftl_source_for()` |

Build names and source paths are enumerated in the [variant catalog](VARIANTS.md). Wrapper names are historical labels; inspect the macros before assigning experimental meaning.

## 3. Timing and device geometry / 时序与设备参数

| Files | Responsibility |
| --- | --- |
| [ssd_die.c](../ssd_die.c) | Die-contention timing and counters; selected by `build_die.sh` |
| [ssd.c](../ssd.c), [ssd.h](../ssd.h) | SSD model and shared structures |
| [ssd_config.h](../ssd_config.h) | Current SLC capacity fraction and timing/model defaults |
| [channel_model.c](../channel_model.c), [channel_model.h](../channel_model.h) | Channel request scheduling and timing |

## 4. Workload families / 工作负载

| Family | Entry points |
| --- | --- |
| SQLite pageflow | Root `sqlite_append_die_affinity*.c`, `sqlite_fragment_die_test*.sh` |
| SQLite historical distributions | Root and `evaluation/` `sqlite_fragment_{zipf,normal,exp}.sh`, `evaluation/sqlite/` |
| fio | `fio_fragment_die_latency_mixed.sh`, `fio_latency_v2_mixed.fio`, `run_sqlite_then_fio_die_variants.sh` |
| Filebench | `fileserver_*.sh`, `fileserver_*.f`, `fileserver_*access.py`, `fileserver_file_tier.py` |
| Historical device/layout helpers | `evaluation/{NVMe,SATA,alignment,pseudo,interface,hypothetical}*` |

See [evaluation/README.md](../evaluation/README.md) for workload semantics and path expectations. The tracked SQLite executable is a historical binary; the workspace tool uses C sources rather than distributing it as a portable build.

## 5. Analysis, results and validation / 结果分析

| Entry point | Responsibility |
| --- | --- |
| [analyze_latency_run.py](../analyze_latency_run.py) | SQLite summaries, prewarm checks, kernel signals and mechanism verdicts |
| [fio_validate_latency_mixed_json.py](../fio_validate_latency_mixed_json.py) | Rejects incomplete fio I/O volume |
| [fio_summarize_latency_mixed.py](../fio_summarize_latency_mixed.py) | General fio runner summaries, including newer aggregate sidecars |
| [tools/reproduce_results.py](../tools/reproduce_results.py) | Verified archive analysis, including older non-aggregate sidecars |
| [tools/plot_results.py](../tools/plot_results.py) | Matplotlib figures from verified archived values |
| [results/](../results/README.md) | Raw evidence, import manifest, tables and figures |
| [test_analyze_latency_run.py](../test_analyze_latency_run.py), [tools/test_artifact_tools.py](../tools/test_artifact_tools.py) | Parser regressions, evidence validation and assembly checks |

The CI workflow runs portable archive checks. It does not compile a kernel module or execute a storage workload.
