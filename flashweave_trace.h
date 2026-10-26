/* SPDX-License-Identifier: GPL-2.0-only */
/* Reservation observations only: no event asserts physical NAND completion.
 * Model timestamps use the existing I/O model clock; *_ns call/issue/record
 * timestamps use ktime_get_ns(). Do not subtract across those clocks.
 */
#undef TRACE_SYSTEM
#define TRACE_SYSTEM flashweave

#ifndef _FLASHWEAVE_TRACE_TYPES_H
#define _FLASHWEAVE_TRACE_TYPES_H
#include <linux/types.h>

#define FW_VALID_ID                 (1U << 0)
#define FW_VALID_PPA                (1U << 1)
#define FW_VALID_ZONE               (1U << 2)
#define FW_VALID_READY              (1U << 3)
#define FW_VALID_MODEL_START        (1U << 4)
#define FW_VALID_MODEL_END          (1U << 5)
#define FW_VALID_RESERVATION        (1U << 6)
#define FW_VALID_COMPLETION_TARGET  (1U << 7)
#define FW_FLAG_PARENT_UNSUPPORTED  (1U << 0)
#define FW_FLAG_START_UNOBSERVED    (1U << 1)
#define FW_FLAG_STANDALONE          (1U << 2)
#define FW_FLAG_LUN_INCLUDES_CHANNEL (1U << 3)

#define FW_WORKER_ID                (1U << 0)
#define FW_WORKER_PRESSURE          (1U << 1)
#define FW_WORKER_BUDGET            (1U << 2)
#define FW_WORKER_MOVED             (1U << 3)
#define FW_WORKER_QUEUE_RESULT      (1U << 4)
#define FW_WORKER_PRIORITY_STATE    (1U << 5)
#define FW_WORKER_ACTIVE_READS      (1U << 6)
#define FW_WORKER_PHASE             (1U << 7)

enum fw_trace_decision {
 FW_DECISION_KICK = 0, FW_DECISION_YIELD = 1, FW_DECISION_REQUEUE = 2,
 FW_DECISION_FORCED = 3, FW_DECISION_EMERGENCY = 4,
 FW_DECISION_PASS_BEGIN = 5, FW_DECISION_PASS_END = 6,
 FW_DECISION_SKIP = 7,
};
/* IDs are unique only within one module load; no host-request parent exists. */
u64 flashweave_next_trace_id(void);

struct fw_nand_schedule {
	u64 call_ns;
	u64 issue_ns;
	u64 record_ns;
	u64 op_id;
	int ns_id;
	int ftl_id;
	int cmd;
	int type;
	int priority;
	u32 valid_mask;
	u32 flags;
	int ch;
	int lun;
	int plane;
	int block;
	int page;
	int zone;
	u64 xfer_bytes;
	int status;
	u64 cmd_ready_ns;
	u64 model_start_ns;
	u64 model_end_ns;
	u64 reservation_end_ns;
	u64 completion_target_ns;
	u64 old_hp_ns;
	u64 old_lp_ns;
	u64 old_normal_ns;
	u64 new_hp_ns;
	u64 new_lp_ns;
	u64 new_normal_ns;
};

struct fw_resource_schedule {
	u64 call_ns;
	u64 issue_ns;
	u64 record_ns;
	u64 op_id;
	int ns_id;
	int ftl_id;
	int cmd;
	int type;
	int priority;
	u32 valid_mask;
	u32 flags;
	int ch;
	int lun;
	int plane;
	int block;
	int page;
	int zone;
	u64 xfer_bytes;
	int resource;
	u32 segment_id;
	u64 ready_ns;
	u64 model_start_ns;
	u64 model_end_ns;
	u64 old_hp_ns;
	u64 old_lp_ns;
	u64 old_normal_ns;
	u64 new_hp_ns;
	u64 new_lp_ns;
	u64 new_normal_ns;
};

struct fw_worker_decision {
	u64 call_ns;
	u64 record_ns;
	u64 op_id;
	int ns_id;
	int ftl_id;
	int family;
	int worker_kind;
	int decision;
	int site;
	int pass_index;
	u32 valid_mask;
	u32 flags;
	int phase_enabled;
	int pressure;
	s64 free_lines;
	s64 total_lines;
	u32 high_watermark;
	u32 low_watermark;
	u32 target_watermark;
	int gate;
	s64 tokens;
	int streak;
	int active_reads;
	int force_active;
	u32 page_bytes;
	s64 budget_pages;
	s64 moved_pages;
	s64 retired_pages_delta;
	int queue_result;
	int catchup_passes;
};

#endif /* _FLASHWEAVE_TRACE_TYPES_H */

#if !defined(_FLASHWEAVE_TRACE_H) || defined(TRACE_HEADER_MULTI_READ)
#define _FLASHWEAVE_TRACE_H
#include <linux/tracepoint.h>

TRACE_EVENT(nand_schedule,
	TP_PROTO(const struct fw_nand_schedule *v),
	TP_ARGS(v),
	TP_STRUCT__entry(
		__field(u64, call_ns)
		__field(u64, issue_ns)
		__field(u64, record_ns)
		__field(u64, op_id)
		__field(int, ns_id)
		__field(int, ftl_id)
		__field(int, cmd)
		__field(int, type)
		__field(int, priority)
		__field(u32, valid_mask)
		__field(u32, flags)
		__field(int, ch)
		__field(int, lun)
		__field(int, plane)
		__field(int, block)
		__field(int, page)
		__field(int, zone)
		__field(u64, xfer_bytes)
		__field(int, status)
		__field(u64, cmd_ready_ns)
		__field(u64, model_start_ns)
		__field(u64, model_end_ns)
		__field(u64, reservation_end_ns)
		__field(u64, completion_target_ns)
		__field(u64, old_hp_ns)
		__field(u64, old_lp_ns)
		__field(u64, old_normal_ns)
		__field(u64, new_hp_ns)
		__field(u64, new_lp_ns)
		__field(u64, new_normal_ns)
	),
	TP_fast_assign(
		__entry->call_ns = v->call_ns;
		__entry->issue_ns = v->issue_ns;
		__entry->record_ns = v->record_ns;
		__entry->op_id = v->op_id;
		__entry->ns_id = v->ns_id;
		__entry->ftl_id = v->ftl_id;
		__entry->cmd = v->cmd;
		__entry->type = v->type;
		__entry->priority = v->priority;
		__entry->valid_mask = v->valid_mask;
		__entry->flags = v->flags;
		__entry->ch = v->ch;
		__entry->lun = v->lun;
		__entry->plane = v->plane;
		__entry->block = v->block;
		__entry->page = v->page;
		__entry->zone = v->zone;
		__entry->xfer_bytes = v->xfer_bytes;
		__entry->status = v->status;
		__entry->cmd_ready_ns = v->cmd_ready_ns;
		__entry->model_start_ns = v->model_start_ns;
		__entry->model_end_ns = v->model_end_ns;
		__entry->reservation_end_ns = v->reservation_end_ns;
		__entry->completion_target_ns = v->completion_target_ns;
		__entry->old_hp_ns = v->old_hp_ns;
		__entry->old_lp_ns = v->old_lp_ns;
		__entry->old_normal_ns = v->old_normal_ns;
		__entry->new_hp_ns = v->new_hp_ns;
		__entry->new_lp_ns = v->new_lp_ns;
		__entry->new_normal_ns = v->new_normal_ns;
	),
	TP_printk("schema=1 call_ns=%llu issue_ns=%llu record_ns=%llu op_id=%llu ns_id=%d ftl_id=%d cmd=%d type=%d priority=%d valid_mask=%u flags=%u ch=%d lun=%d plane=%d block=%d page=%d zone=%d xfer_bytes=%llu status=%d cmd_ready_ns=%llu model_start_ns=%llu model_end_ns=%llu reservation_end_ns=%llu completion_target_ns=%llu old_hp_ns=%llu old_lp_ns=%llu old_normal_ns=%llu new_hp_ns=%llu new_lp_ns=%llu new_normal_ns=%llu",
		(unsigned long long)__entry->call_ns,
		(unsigned long long)__entry->issue_ns,
		(unsigned long long)__entry->record_ns,
		(unsigned long long)__entry->op_id,
		__entry->ns_id,
		__entry->ftl_id,
		__entry->cmd,
		__entry->type,
		__entry->priority,
		__entry->valid_mask,
		__entry->flags,
		__entry->ch,
		__entry->lun,
		__entry->plane,
		__entry->block,
		__entry->page,
		__entry->zone,
		(unsigned long long)__entry->xfer_bytes,
		__entry->status,
		(unsigned long long)__entry->cmd_ready_ns,
		(unsigned long long)__entry->model_start_ns,
		(unsigned long long)__entry->model_end_ns,
		(unsigned long long)__entry->reservation_end_ns,
		(unsigned long long)__entry->completion_target_ns,
		(unsigned long long)__entry->old_hp_ns,
		(unsigned long long)__entry->old_lp_ns,
		(unsigned long long)__entry->old_normal_ns,
		(unsigned long long)__entry->new_hp_ns,
		(unsigned long long)__entry->new_lp_ns,
		(unsigned long long)__entry->new_normal_ns
	)
);

TRACE_EVENT(resource_schedule,
	TP_PROTO(const struct fw_resource_schedule *v),
	TP_ARGS(v),
	TP_STRUCT__entry(
		__field(u64, call_ns)
		__field(u64, issue_ns)
		__field(u64, record_ns)
		__field(u64, op_id)
		__field(int, ns_id)
		__field(int, ftl_id)
		__field(int, cmd)
		__field(int, type)
		__field(int, priority)
		__field(u32, valid_mask)
		__field(u32, flags)
		__field(int, ch)
		__field(int, lun)
		__field(int, plane)
		__field(int, block)
		__field(int, page)
		__field(int, zone)
		__field(u64, xfer_bytes)
		__field(int, resource)
		__field(u32, segment_id)
		__field(u64, ready_ns)
		__field(u64, model_start_ns)
		__field(u64, model_end_ns)
		__field(u64, old_hp_ns)
		__field(u64, old_lp_ns)
		__field(u64, old_normal_ns)
		__field(u64, new_hp_ns)
		__field(u64, new_lp_ns)
		__field(u64, new_normal_ns)
	),
	TP_fast_assign(
		__entry->call_ns = v->call_ns;
		__entry->issue_ns = v->issue_ns;
		__entry->record_ns = v->record_ns;
		__entry->op_id = v->op_id;
		__entry->ns_id = v->ns_id;
		__entry->ftl_id = v->ftl_id;
		__entry->cmd = v->cmd;
		__entry->type = v->type;
		__entry->priority = v->priority;
		__entry->valid_mask = v->valid_mask;
		__entry->flags = v->flags;
		__entry->ch = v->ch;
		__entry->lun = v->lun;
		__entry->plane = v->plane;
		__entry->block = v->block;
		__entry->page = v->page;
		__entry->zone = v->zone;
		__entry->xfer_bytes = v->xfer_bytes;
		__entry->resource = v->resource;
		__entry->segment_id = v->segment_id;
		__entry->ready_ns = v->ready_ns;
		__entry->model_start_ns = v->model_start_ns;
		__entry->model_end_ns = v->model_end_ns;
		__entry->old_hp_ns = v->old_hp_ns;
		__entry->old_lp_ns = v->old_lp_ns;
		__entry->old_normal_ns = v->old_normal_ns;
		__entry->new_hp_ns = v->new_hp_ns;
		__entry->new_lp_ns = v->new_lp_ns;
		__entry->new_normal_ns = v->new_normal_ns;
	),
	TP_printk("schema=1 call_ns=%llu issue_ns=%llu record_ns=%llu op_id=%llu ns_id=%d ftl_id=%d cmd=%d type=%d priority=%d valid_mask=%u flags=%u ch=%d lun=%d plane=%d block=%d page=%d zone=%d xfer_bytes=%llu resource=%d segment_id=%u ready_ns=%llu model_start_ns=%llu model_end_ns=%llu old_hp_ns=%llu old_lp_ns=%llu old_normal_ns=%llu new_hp_ns=%llu new_lp_ns=%llu new_normal_ns=%llu",
		(unsigned long long)__entry->call_ns,
		(unsigned long long)__entry->issue_ns,
		(unsigned long long)__entry->record_ns,
		(unsigned long long)__entry->op_id,
		__entry->ns_id,
		__entry->ftl_id,
		__entry->cmd,
		__entry->type,
		__entry->priority,
		__entry->valid_mask,
		__entry->flags,
		__entry->ch,
		__entry->lun,
		__entry->plane,
		__entry->block,
		__entry->page,
		__entry->zone,
		(unsigned long long)__entry->xfer_bytes,
		__entry->resource,
		__entry->segment_id,
		(unsigned long long)__entry->ready_ns,
		(unsigned long long)__entry->model_start_ns,
		(unsigned long long)__entry->model_end_ns,
		(unsigned long long)__entry->old_hp_ns,
		(unsigned long long)__entry->old_lp_ns,
		(unsigned long long)__entry->old_normal_ns,
		(unsigned long long)__entry->new_hp_ns,
		(unsigned long long)__entry->new_lp_ns,
		(unsigned long long)__entry->new_normal_ns
	)
);

TRACE_EVENT(worker_decision,
	TP_PROTO(const struct fw_worker_decision *v),
	TP_ARGS(v),
	TP_STRUCT__entry(
		__field(u64, call_ns)
		__field(u64, record_ns)
		__field(u64, op_id)
		__field(int, ns_id)
		__field(int, ftl_id)
		__field(int, family)
		__field(int, worker_kind)
		__field(int, decision)
		__field(int, site)
		__field(int, pass_index)
		__field(u32, valid_mask)
		__field(u32, flags)
		__field(int, phase_enabled)
		__field(int, pressure)
		__field(s64, free_lines)
		__field(s64, total_lines)
		__field(u32, high_watermark)
		__field(u32, low_watermark)
		__field(u32, target_watermark)
		__field(int, gate)
		__field(s64, tokens)
		__field(int, streak)
		__field(int, active_reads)
		__field(int, force_active)
		__field(u32, page_bytes)
		__field(s64, budget_pages)
		__field(s64, moved_pages)
		__field(s64, retired_pages_delta)
		__field(int, queue_result)
		__field(int, catchup_passes)
	),
	TP_fast_assign(
		__entry->call_ns = v->call_ns;
		__entry->record_ns = v->record_ns;
		__entry->op_id = v->op_id;
		__entry->ns_id = v->ns_id;
		__entry->ftl_id = v->ftl_id;
		__entry->family = v->family;
		__entry->worker_kind = v->worker_kind;
		__entry->decision = v->decision;
		__entry->site = v->site;
		__entry->pass_index = v->pass_index;
		__entry->valid_mask = v->valid_mask;
		__entry->flags = v->flags;
		__entry->phase_enabled = v->phase_enabled;
		__entry->pressure = v->pressure;
		__entry->free_lines = v->free_lines;
		__entry->total_lines = v->total_lines;
		__entry->high_watermark = v->high_watermark;
		__entry->low_watermark = v->low_watermark;
		__entry->target_watermark = v->target_watermark;
		__entry->gate = v->gate;
		__entry->tokens = v->tokens;
		__entry->streak = v->streak;
		__entry->active_reads = v->active_reads;
		__entry->force_active = v->force_active;
		__entry->page_bytes = v->page_bytes;
		__entry->budget_pages = v->budget_pages;
		__entry->moved_pages = v->moved_pages;
		__entry->retired_pages_delta = v->retired_pages_delta;
		__entry->queue_result = v->queue_result;
		__entry->catchup_passes = v->catchup_passes;
	),
	TP_printk("schema=1 call_ns=%llu record_ns=%llu op_id=%llu ns_id=%d ftl_id=%d family=%d worker_kind=%d decision=%d site=%d pass_index=%d valid_mask=%u flags=%u phase_enabled=%d pressure=%d free_lines=%lld total_lines=%lld high_watermark=%u low_watermark=%u target_watermark=%u gate=%d tokens=%lld streak=%d active_reads=%d force_active=%d page_bytes=%u budget_pages=%lld moved_pages=%lld retired_pages_delta=%lld queue_result=%d catchup_passes=%d",
		(unsigned long long)__entry->call_ns,
		(unsigned long long)__entry->record_ns,
		(unsigned long long)__entry->op_id,
		__entry->ns_id,
		__entry->ftl_id,
		__entry->family,
		__entry->worker_kind,
		__entry->decision,
		__entry->site,
		__entry->pass_index,
		__entry->valid_mask,
		__entry->flags,
		__entry->phase_enabled,
		__entry->pressure,
		(long long)__entry->free_lines,
		(long long)__entry->total_lines,
		__entry->high_watermark,
		__entry->low_watermark,
		__entry->target_watermark,
		__entry->gate,
		(long long)__entry->tokens,
		__entry->streak,
		__entry->active_reads,
		__entry->force_active,
		__entry->page_bytes,
		(long long)__entry->budget_pages,
		(long long)__entry->moved_pages,
		(long long)__entry->retired_pages_delta,
		__entry->queue_result,
		__entry->catchup_passes
	)
);

#endif /* _FLASHWEAVE_TRACE_H */

#undef TRACE_INCLUDE_PATH
#define TRACE_INCLUDE_PATH .
#undef TRACE_INCLUDE_FILE
#define TRACE_INCLUDE_FILE flashweave_trace
#include <trace/define_trace.h>
