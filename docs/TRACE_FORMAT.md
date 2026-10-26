# FlashWeave reservation trace format (schema 1)

`flashweave_trace.h` defines standard Linux `TRACE_EVENT` events under
`TRACE_SYSTEM=flashweave`. `ssd_die.c` is the sole `CREATE_TRACE_POINTS`
translation unit. The staged module Makefile needs `ccflags-y += -I$(src)`
because `TRACE_INCLUDE_PATH` is `.`. Events are disabled by default and use
`trace_*_enabled()` static-key checks before collecting snapshots, timestamps,
or operation IDs. Enabling tracing adds work inside timing locks and can perturb
wall-clock behavior; obtain performance results separately with tracing disabled.

These events report **software decisions and final model reservations**. They do
not report physical NAND start/completion, active-hardware preemption, request
completion, complete maintenance debt, or a drained device. No scheduling policy
or reservation-commit helper was changed to add them.

## Clocks, identity, validity

All text records start with `schema=1`, followed by decimal `key=value` fields.
The tracefs common prefix is separate from this payload.

- `call_ns`, `issue_ns`, and `record_ns` use `ktime_get_ns()`: the observation
  entry, timing-lock decision point, and snapshot emission point. Worker events
  have `call_ns`/`record_ns` only. A GC timing-disabled NAND call has no timing-lock
  decision and reports `issue_ns=0`.
- Ready/model/reservation/completion-target timestamps are the existing I/O
  model's timestamps. Inputs can be supplied by callers, or derived from
  `__get_ioclock()`. Do not subtract these values from the `ktime_get_ns()` fields
  or the tracefs prefix clock.
- `op_id` is allocated from one module-wide `atomic64_t`. It starts at 1 on each
  module load. Capture a single load session per trace, and retain module/source
  provenance; an ID alone cannot join different runs or module reloads.
- A NAND call and its internally invoked channel/PCIe/LUN resource reservations
  share `op_id`. A directly called write-buffer/PCIe reservation has its own ID
  and no associated NAND record. `ns_id` is the namespace ID; `ftl_id` is the
  namespace-local FTL partition index. Other, uninstrumented FTL families retain
  identity `-1` with the identity-valid bit clear.
- Worker invocation IDs share the allocator, but **there is no reliable link from
  a worker invocation or a host request to NAND calls**. All events set
  `PARENT_UNSUPPORTED`; no parent ID is fabricated. SLC pass begin/end share an
  invocation `op_id` and `pass_index` when tracing is continuously enabled from
  worker entry. Enabling/disabling tracing during a pass can leave unmatched
  boundary records; reject claims requiring a complete pair in that case.
- `valid_mask` determines which optional values may be interpreted. Unsupported
  signed address/state/count fields use `-1`; unsupported unsigned timestamps
  use 0 with their valid bit clear. Zero with a valid bit set can be a real zero.
  State snapshots are not synchronization barriers. In particular, worker
  atomics and free-line snapshots need not represent one simultaneous state.

Common NAND/resource valid-mask bits:

| Value | Name | Meaning |
| --- | --- | --- |
| 1 | `FW_VALID_ID` | Namespace and FTL identity initialized |
| 2 | `FW_VALID_PPA` | PPA passed range validation |
| 4 | `FW_VALID_ZONE` | Exact latency-table index used for a QLC read |
| 8 | `FW_VALID_READY` | Ready timestamp observed |
| 16 | `FW_VALID_MODEL_START` | Model start observed at this call site |
| 32 | `FW_VALID_MODEL_END` | Model end observed at this call site |
| 64 | `FW_VALID_RESERVATION` | All six old/new reservation timestamps observed |
| 128 | `FW_VALID_COMPLETION_TARGET` | NAND function's returned model target observed |

Common flags:

| Value | Name | Meaning |
| --- | --- | --- |
| 1 | `FW_FLAG_PARENT_UNSUPPORTED` | No host-request/worker parent association |
| 2 | `FW_FLAG_START_UNOBSERVED` | Normal channel/PCIe credit model does not expose exact start here |
| 4 | `FW_FLAG_STANDALONE` | PCIe reservation has its own ID and no NAND association |
| 8 | `FW_FLAG_LUN_INCLUDES_CHANNEL` | Host-read LUN reservation extends through channel transfer |

## `flashweave:nand_schedule`

Common payload fields are `call_ns issue_ns record_ns op_id ns_id ftl_id cmd type
priority valid_mask flags ch lun plane block page zone xfer_bytes`.
The remaining fields are:

```
status cmd_ready_ns model_start_ns model_end_ns reservation_end_ns
completion_target_ns old_hp_ns old_lp_ns old_normal_ns
new_hp_ns new_lp_ns new_normal_ns
```

`cmd` is the existing command enum: READ=0, WRITE=1, ERASE=2, NOP=3.
`type` is USER_IO=0 or GC_IO=1; it does not identify a finer maintenance origin.
`priority` describes the branch actually selected: normal=0, read-priority=1,
low-priority=2. `ch/lun/plane/block/page` are caller PPA components, not a newly
inferred request address. The validity mask excludes unvalidated addresses.
`zone` is valid only for QLC READ and equals the clamped QLC latency-table index
used by that command. `xfer_bytes` is `ncmd->xfer_size`.

`model_start_ns`/`model_end_ns` bound NAND service in the model.
`reservation_end_ns` is the command's LUN commit point, including host-read
channel transfer where applicable. It can differ from both NAND service end and
`completion_target_ns`, the return value of `ssd_advance_nand_internal()`.
An interleaved PCIe transfer can extend that returned target.
The six reservation fields are the LUN's HP, LP and normal reservation values
before and after this call's final commit, captured while holding its timing lock.

`status` determines interval support:

| Status | Meaning | Supported data |
| --- | --- | --- |
| 0 | Scheduled READ/WRITE/ERASE | Validated PPA, service interval, reservations, returned target |
| 1 | GC NAND timing disabled | Identity if initialized, ready and returned target only; no resource event |
| 2 | NOP | Validated PPA, reservations and returned target; no NAND service interval |
| 3 | Unsupported command | Validated PPA and unchanged reservation snapshot; no service interval or resource event |

The original defensive returns for null/unmapped/out-of-range inputs are kept;
they do not emit a NAND event. Status 1 preserves the original earlier GC-timing
return, so its PPA is not validated or exposed. Reservation-bypass observations
must require valid service-start and reservation fields; status 1 is not evidence
of NAND work.

## `flashweave:resource_schedule`

Common fields are the same as NAND's common fields. The remaining fields are:

```
resource segment_id ready_ns model_start_ns model_end_ns
old_hp_ns old_lp_ns old_normal_ns new_hp_ns new_lp_ns new_normal_ns
```

`resource`: LUN/NAND=0, channel=1, PCIe=2. `segment_id` increments within the
associated NAND context across resource types. Channel and PCIe chunks are
recorded when their reservations are finalized; the LUN resource is recorded
after the command's final LUN commit. File order is observation order, not model
execution order. The records are not separate completion callbacks.

- Resource 0: `model_start_ns` is NAND service start if supported;
  `model_end_ns` is the final LUN commit point, including host-read channel
  transfer when flag 8 is set. NOP has no service start but has a final commit point.
- Resource 1/2 with read/low priority: start and end are the existing explicit
  scheduling variables. Normal channel/PCIe paths call the credit model, which
  returns an end but does not expose an exact internal service start here.
  They have flag 2 set, `model_start_ns=0`, and valid bit 16 clear. `ready_ns`
  remains the incoming request time; it is not relabeled as exact service start.
- A standalone PCIe event has flag 4 set, `cmd=type=-1`, PPA/zone fields `-1`,
  and their valid bits clear. Normally this is write-buffer DMA. If tracing is
  enabled during a NAND call that began with tracing off, an unassociated PCIe
  observation can also be standalone; it must not be paired by proximity.

Resource identity is `(ns_id,ftl_id,ch,lun)` for resource 0 and
`(ns_id,ftl_id,ch)` for resource 1. PCIe is shared by FTL partitions within a
namespace: resource 2 identity is `ns_id`, while its `ftl_id` and PPA describe the
calling operation's origin. They do not denote separate PCIe resource instances.

A start below an earlier LP reservation can be described as **reservation bypass
in this model**. These observations cannot establish physical nonpreemption or
an actual in-flight NAND command's behavior.

## `flashweave:worker_decision`

Payload fields:

```
call_ns record_ns op_id ns_id ftl_id family worker_kind decision site pass_index
valid_mask flags phase_enabled pressure free_lines total_lines high_watermark
low_watermark target_watermark gate tokens streak active_reads force_active
page_bytes budget_pages moved_pages retired_pages_delta queue_result catchup_passes
```

`family` is 1 or 3 for V1/V3. `worker_kind`: unknown nested yield=-1,
SLC maintenance=0, repromotion=1, QLC rebalance=2, foreground maintenance=3.
Only SLC background passes have paired pass events. Shared V3 yield notes and
all three delayed-requeue wrappers are observed; this does not claim complete
pass accounting for repromotion/rebalance.

| Decision | Value | Observation site |
| --- | --- | --- |
| KICK | 0 | `queue_work()` return for SLC work |
| YIELD | 1 | Shared V3 `note_yield()`, after yield/streak counters increment |
| REQUEUE | 2 | Real `queue_delayed_work()` result |
| FORCED | 3 | V3 force threshold accepted, before resetting the yield streak |
| EMERGENCY | 4 | V1 SLC worker skips an emergency pass; V3 permits forced work; or foreground control-tick emergency branch |
| PASS_BEGIN | 5 | SLC migration pass after budget selection, before work |
| PASS_END | 6 | Same pass after migration and optional SLC GC |
| SKIP | 7 | V3 worker sees pressure below BG and returns |

`site`: SLC kick=1; SLC worker=2; shared yield note=3; force helper=4;
SLC/repromotion/rebalance delayed queue=5/6/7; foreground emergency=8.
`pass_index` is 0 for V1, the loop index for V3, and -1 outside a pass.
`pressure` follows source values: IDLE_ONLY=0, BG=1, URGENT=2, EMERGENCY=3;
-1 means unavailable. Preserve the source policy even if a level is unreachable
under the current threshold helper. `free_lines`/`total_lines` come from the
already-collected SLC snapshot; pass end does not collect another snapshot.
Watermarks are the source's SLC migration high watermark, hard-GC low threshold,
and target watermark. Gate/tokens/streak/active/force are independently read
atomic snapshots; V1 priority-specific state is unsupported and uses -1.

Worker valid-mask bits:

| Value | Meaning |
| --- | --- |
| 1 | Namespace/FTL identity initialized |
| 2 | Free/total line snapshot supplied; pressure derived from that snapshot |
| 4 | `budget_pages` available |
| 8 | `moved_pages` available |
| 16 | `queue_result` available (0 already queued/not newly queued, 1 newly queued) |
| 32 | V3 gate/tokens/streak/force-active state available |
| 64 | `active_reads` snapshot available |
| 128 | Test-phase state available |

`page_bytes=ssd->sp.pgsz`; page counts are **mapping pages**, not physical flash
pages or oneshot program units. A successful `migrate_page_to_qlc()` increments
the underlying migration return by one mapping page. SLC pass `moved_pages` is
exactly local `hard_moved + migrate_some_cold_from_slc(...)` where those paths
execute, matching the increment to `slc_maint_pages`. It excludes valid-page
copies inside the subsequent GC call, erase work, host writes and whole-system
maintenance completion. A pass can move zero pages. `budget_pages` is the pass's
initial source budget; emergency victim creation can perform an additional
superblock migration, so it is not an invariant upper bound on `moved_pages`.
`catchup_passes` is the source-selected catchup count where supplied, otherwise -1.

`retired_pages_delta` is **unsupported for every worker event**, always -1.
There is deliberately no retired-pages valid bit in schema 1. No NAND/resource
event supplies retirement either. `moved_pages` must not be substituted for
retired pages, work debt, reclaimed capacity, or a drain result.

## Capture and analysis CLI

The [experiment protocol](EXPERIMENTS.md) manages collection only for explicit
--trace-mode diag runs. After B succeeds it starts the collector; after Q/final
it stops collection before module unload. Formal performance runs keep tracing
off. The standalone control interface, for an already-loaded diagnostic module,
is:

    sudo python3 experiments/trace_capture.py start --out <FRESH_CAPTURE_DIR>
    sudo python3 experiments/trace_capture.py stop --out <SAME_CAPTURE_DIR>
    python3 experiments/trace_analyze.py --capture <RUN_DIR>/trace --out <FRESH_ANALYSIS_DIR>

start/stop use a private tracefs instance and leave the global tracer untouched.
Save result.json, trace.txt, event_formats, per_cpu_before/after and controller
logs. trace.remaining.txt is a non-consuming remainder snapshot; do not append
it to trace.txt and double-count records. Empty streams, event-ID/layout changes,
per-CPU loss or undrained entries, and collection caps fail the capture. Default
text cap is 8 GiB; protocol --trace-max-bytes can change it. Large traces are
streamed and the analyzer uses a temporary on-disk association index. Preserve
all raw evidence when analysis.json reports incomplete_or_invalid. Analysis
produces analysis.json and analysis.txt; valid_reservation_observations is not a
causal mechanism, hardware non-preemption or full-drain certificate.

## Verification scope

Run `python3 tests/test_trace_observation.py` on a host with Python, a C compiler,
and the repository history containing baseline commit
`1cae660aec598f7bdf755df12e1d00f285ffd857`.
The checks compile the real header's field assignments/printf formats, compare
13 unmodified reservation helpers with that baseline, and execute extracted
original/instrumented NAND/channel/PCIe functions on 32,768 deterministic
operations with tracing off/on under AddressSanitizer and UBSan. They also compile
and execute the extracted V1/V3 worker snapshot helpers. Model resource functions
and kernel facilities are portable stubs. These checks do not validate the full
credit model, real concurrency, tracefs integration, a Linux module build/load,
or hardware behavior. Linux CI and device collection are separate evidence.

Standard tracepoint integration follows the Linux kernel's
[tracepoint documentation](https://docs.kernel.org/trace/tracepoints.html) and
[event tracing documentation](https://docs.kernel.org/trace/events.html).
