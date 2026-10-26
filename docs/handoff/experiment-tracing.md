# Handoff: reproducible ablations and scheduling observations

## Objective

Provide a repository-native path from one machine configuration to a four-cell pilot and five paired seeds (20 runs), with separate diagnostic traces and explicit missing-data boundaries.

## Scope

- Added experiments/ protocol, synchronous fio coordinator, runtime/geometry/source/build gates, global module lease, matrix entry and descriptive paired tables.
- Added opt-in standard flashweave trace events for NAND reservations, channel/PCIe reservations and worker decisions. Existing reservation policy/helpers remain unchanged.
- Added private-instance streaming capture, loss/lifecycle/size/finalization checks and bounded-memory trace analysis.
- Added docs/EXPERIMENTS.md and docs/TRACE_FORMAT.md. Corrected the archived fio3.16 write-span description.
- Preserved the 37 historical raw inputs and their derived outputs; no new device measurements or performance claims.
- Kept full model-drain, hardware non-preemption, deterministic trigger scenarios, heat/relocation and matched application results marked DATA REQUIRED.

## Evidence

Local portable validation on 2026-10-26:

- experiments protocol/summary: 17 unittest cases.
- diagnostic trace fixtures/CLI: 19 unittest cases.
- source-derived trace header/timing/worker observations: 4 unittest cases.
- existing artifact integrity/workspace checks: 9 unittest cases.
- existing application parser regression suite and 37-input/four-derived-file archive check passed.
- Python/shell syntax and git diff whitespace checks passed.
- Source-derived C test compared 32,768 deterministic operations with trace off/on against the original reservation implementation using ASan/UBSan. This is not a full kernel, credit-model, concurrency or hardware test.
- GitHub CI adds Python3.10/3.12 checks and a four-variant Linux distribution-header compile job; inspect the PR's current checks for its actual result.
- No local Linux kernel build/load, actual tracefs collection, raw-device fio or new performance repetitions were run.

## Independent review

Reviewed protocol boundaries, module/binary identity, loader/measurement order, trace lifecycle and C reservation preservation.

Fixed findings included fio3.16 shell-hook synchronization/exit propagation, missing counter resets, OP-adjusted logical capacity, buffer-loss detection, bounded diagnostic memory, collector finalization, source/build binding and trace-instance cleanup ownership.

## Residual risk

Runtime requires the compatible die-affinity Linux environment, matching running-kernel headers, actual reserved memory and a dedicated emulated namespace. Distribution-header CI compilation does not certify that environment.

Trace timestamps describe source decisions/model reservations. Unknown credit-model start, host/worker parent relationships and retired debt are marked unsupported. Moved mapping pages are not a full-drain result. Five paired seeds are descriptive replication, not automatic statistical significance.

## Next step

Fill ignored experiments/host_config.sh, prepare a fresh clean workspace, preview/build, run and inspect the four-cell pilot, then run the formal matrix. Use separate diag runs and trace analysis for mechanism evidence. Review raw manifests/kernel diagnostics before promoting a new dataset into results/.

