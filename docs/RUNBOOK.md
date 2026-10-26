# Reproduction runbook / 复现指南

For new matched four-cell ablation and repeated experiments, use the [native experiment protocol](EXPERIMENTS.md). It freezes sources/builds, executes synchronous phases, validates histogram counts and supports separate trace diagnostics. The commands below also document historical/manual paths.

## A. Verify archived results on Linux or macOS

Requirements: Python 3.9+. These steps do not require root, a kernel module or a block device.

```bash
python3 tools/reproduce_results.py --check
python3 test_analyze_latency_run.py
python3 tools/test_artifact_tools.py
```

`--check` verifies raw hashes and compares regenerated table contents with committed outputs. To regenerate files, omit `--check`. To regenerate figures, install `tools/requirements-results.txt` in a virtual environment and run `tools/plot_results.py`.

## B. Assemble build inputs

```bash
python3 tools/prepare_workspace.py --output .workspaces/nvmevirt
```

The tool refuses an existing output directory. It creates:

```text
.workspaces/nvmevirt/
├── nvmevirt_DA/          # complete foundation + root module overlays
│   ├── Makefile          # forwards NVMEV_EXTRA_CFLAGS to ccflags-y
│   ├── build_die.sh
│   └── conv_ftl*.c
├── evaluation/          # inherited helpers + current root workloads
│   ├── commonvariables.sh
│   ├── fio_fragment_die_latency_mixed.sh
│   └── sqlite/          # C sources at paths expected by runners
└── workspace_inputs.json # SHA-256 for every staged input
```

The repository root alone is not a complete module checkout: it lacks the Makefile and some support sources. The inherited Makefile also does not consume `NVMEV_EXTRA_CFLAGS`; the staged Makefile adds that forwarding so integrated ablations are not silently compiled with default flags. Tracked originals remain unchanged.

Workspace assembly and required paths are tested. This does not certify that every historical variant compiles against every kernel.

## C. Linux machine prerequisites

Use a dedicated Linux experiment machine, a compatible die-affinity kernel, matching kernel headers, GCC/make, Python, fio and SQLite development libraries. Filebench is needed only for its workloads.

The inherited [module README](../nvmevirt_DA/README.md) points to a modified Linux 5.15 kernel interface. The repository does not vendor that kernel; availability of the historical external download is not guaranteed. Stock-kernel compatibility has not been established here.

Reserve host memory for NVMeVirt using a machine-specific boot configuration. The memory reservation and `insmod` examples in the inherited README assume a large NUMA machine; determine valid physical ranges and CPU affinity for the actual host. Record kernel, memory map, NUMA binding and CPU assignments with the run.

## D. Build selected variants

On the configured Linux machine:

```bash
cd .workspaces/nvmevirt/nvmevirt_DA
bash build_die.sh die_i_base die_i_all
```

For the archived comparison's variant families:

```bash
bash build_die.sh die_latency1_norp_sb die_latency1_qlc_all_norp_sb die_latency2_qlc_all_norp_sb die_latency3_qlc_all_norp_sb
```

`build_die.sh` temporarily selects `ssd_die.c` and the mapped FTL, calls Kbuild, and saves `nvmev_<variant>.ko`. It restores source files on exit. Keep the selected module hashes and build logs; the current wrapper implementation may differ from the historical binary used in an archive.

## E. Configure and run a new device experiment

From the staged `evaluation/` directory, inspect `commonvariables.sh`, `nvmevstart_on.sh`, `setdevice.sh` and the chosen runner. They contain inherited device names, reserved-memory offsets and CPU settings. Some paths format or repartition a device. Set them for the actual emulated device before executing a workload.

The fio runner supports `FIO_DRY_RUN=1` to generate jobs without executing fio/device setup. It is a configuration preview, not a measured run:

```bash
cd ../evaluation
FIO_DRY_RUN=1 \
VARIANTS='die_latency1_norp_sb die_latency2_qlc_all_norp_sb' \
FIO_ACCESS_DIST_LIST=zipf FIO_RW_RATIOS=10:1 \
FIO_MEASURE_READ_BYTES=5G FIO_MEASURE_WRITE_BYTES=512M \
bash fio_fragment_die_latency_mixed.sh
```

This selects the archived byte volumes but does not reconstruct the original machine, binary or random stream. Runner defaults have changed; inspect generated jobs rather than assuming defaults reproduce a historical run. For the archived fio-3.16 path, offset=8 GiB and size/end=9,126,805,504 bytes describe a 512 MiB address span, [8, 8.5 GiB), because that implementation subtracts offset from size. Pin the version; do not treat the absolute size/end value as the span.

Before a measured run, record the exact generated job, explicit random seeds, source/module hashes, runtime controls and scoped kernel capture. Capture counter baselines, measured-phase counters and post-drain counters separately. Use matched repeated seeds across variants. Validate requested versus completed bytes before calculating latency differences.

## F. Interpret and retain outputs

The general fio tools support current runner output; `tools/reproduce_results.py` handles the curated archive and older sidecar naming explicitly. SQLite analysis reports prewarm and completion evidence rather than treating every elapsed time as valid.

Add each new dataset under a distinct `results/raw/` collection with a manifest and documented command/configuration. Do not overwrite the frozen historical inputs. Record failures, missing counters and latency regressions alongside improvements.

## Current validation boundary

Portable parser tests, archive integrity, table reproduction, figure generation, workspace assembly and two fio dry-run job-generation cases were checked during this import. No new Linux kernel build, module load, device formatting or storage benchmark was executed. Reproducing archived tables and reproducing device behavior are separate stages.
