#!/usr/bin/env python3
"""Portable source-extraction checks; does not build/load a Linux kernel module.

Compares unmodified source at BASELINE against the instrumented NAND/channel/PCIe
functions using identical deterministic model stubs, tracing both off and on.
The trace header's actual field assignments and printf formats are compiled.
The credit-model implementation, concurrency and tracefs integration are outside
this test. A Linux module build and hardware trace collection remain required.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "1cae660aec598f7bdf755df12e1d00f285ffd857"

TYPES = """#include <stdint.h>
#include <stdbool.h>
typedef unsigned long long u64;
typedef long long s64;
typedef unsigned int u32;
"""
TRACE_STUB = r'''
#include <assert.h>
#include <stdarg.h>
static int fw_enabled;
static unsigned long fw_event_count;
static void fw_printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void fw_printf(const char *fmt, ...) { (void)fmt; }
static void fw_validate_nand_schedule(const struct fw_nand_schedule *v) {
 assert(v->op_id && v->call_ns <= v->record_ns);
 assert(v->flags & FW_FLAG_PARENT_UNSUPPORTED);
 if (v->status == 1) {
  assert(v->issue_ns == 0);
  assert(!(v->valid_mask & (FW_VALID_PPA|FW_VALID_MODEL_START|FW_VALID_RESERVATION)));
 } else {
  assert(v->issue_ns >= v->call_ns && v->record_ns >= v->issue_ns);
  assert(v->valid_mask & FW_VALID_RESERVATION);
  if (v->status == 0) {
   assert(v->valid_mask & FW_VALID_MODEL_START);
   assert(v->valid_mask & FW_VALID_MODEL_END);
   assert(v->model_end_ns >= v->model_start_ns);
   assert(v->reservation_end_ns >= v->model_end_ns);
  }
 }
}
static void fw_validate_resource_schedule(const struct fw_resource_schedule *v) {
 assert(v->op_id && v->issue_ns >= v->call_ns && v->record_ns >= v->issue_ns);
 assert(v->valid_mask & FW_VALID_RESERVATION);
 assert(v->valid_mask & FW_VALID_MODEL_END);
 if (v->valid_mask & FW_VALID_MODEL_START)
  assert(v->model_end_ns >= v->model_start_ns);
 else assert(v->model_start_ns == 0);
 if (v->flags & FW_FLAG_STANDALONE) {
  assert(v->resource == 2 && v->cmd == -1 && v->type == -1);
  assert(!(v->valid_mask & FW_VALID_PPA));
 }
}
static void fw_validate_worker_decision(const struct fw_worker_decision *v) {
 assert(v->op_id && v->call_ns <= v->record_ns);
 assert(v->retired_pages_delta == -1);
 if (!(v->valid_mask & FW_WORKER_PRESSURE))
  assert(v->free_lines == -1 && v->total_lines == -1);
 if (!(v->valid_mask & FW_WORKER_MOVED)) assert(v->moved_pages == -1);
 if (!(v->valid_mask & FW_WORKER_PRIORITY_STATE))
  assert(v->gate == -1 && v->tokens == -1 && v->streak == -1 && v->force_active == -1);
}
#define TP_PROTO(...) (__VA_ARGS__)
#define TP_ARGS(...) __VA_ARGS__
#define TP_STRUCT__entry(...) __VA_ARGS__
#define TP_fast_assign(...) do { __VA_ARGS__ } while (0)
#define TP_printk(...) fw_printf(__VA_ARGS__)
#define __field(t,n) t n;
#define TRACE_EVENT(n,p,a,e,c,k) \
 struct fw_entry_##n { e }; \
 static inline bool trace_##n##_enabled(void) { return fw_enabled; } \
 static inline void trace_##n p { struct fw_entry_##n storage = {0}; \
 struct fw_entry_##n *__entry = &storage; c; k; \
 fw_validate_##n(v); ++fw_event_count; }
'''
MODEL_STUB = r'''
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#include <assert.h>
typedef long long atomic64_t;
typedef long atomic_long_t;
#define ATOMIC64_INIT(v) (v)
#define atomic64_inc(p) (++*(p))
#define atomic64_add(v,p) (*(p)+=(v))
#define atomic64_inc_return(p) (++*(p))
#define atomic_long_inc(p) (++*(p))
#define atomic_long_add(v,p) (*(p)+=(v))
#define unlikely(v) (v)
#define spin_lock(p) ((void)(p))
#define spin_unlock(p) ((void)(p))
#define max(a,b) ((a)>(b)?(a):(b))
#define min(a,b) ((a)<(b)?(a):(b))
#define DIV_ROUND_UP(a,b) (((a)+(b)-1)/(b))
#define UNIT_XFER_SIZE 4096
#define U64_MAX UINT64_MAX
#define UNMAPPED_PPA UINT64_MAX
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define KB(a) ((a)*1024)
#define NVMEV_ERROR(...) ((void)0)
#define NVMEV_DEBUG(...) ((void)0)
#define NAND_READ 0
#define NAND_WRITE 1
#define NAND_ERASE 2
#define NAND_NOP 3
#define USER_IO 0
#define GC_IO 1
struct ppa { union { struct {
 uint64_t pg:8, blk:8, pl:2, lun:2, ch:2, reserved:42;
 } g; uint64_t ppa; }; };
struct nand_cmd {
 int type,cmd; uint64_t xfer_size,stime; bool interleave_pci_dma; struct ppa *ppa;
 atomic64_t *tracked_read_die_conflicts,*tracked_read_die_wait_ns;
 atomic64_t *tracked_read_lp_bypass_ops,*tracked_read_lp_bypass_ns;
};
struct channel_model { unsigned int xfer_lat; };
struct nand_page { unsigned int qlc_latency_zone; };
struct nand_lun { uint64_t hp_next_lun_avail_time,lp_next_lun_avail_time,next_lun_avail_time; int timing_lock; };
struct ssd_channel { uint64_t hp_next_ch_avail_time,lp_next_ch_avail_time,next_ch_avail_time; int timing_lock; struct nand_lun lun[2]; struct channel_model *perf_model; };
struct ssd_pcie { uint64_t hp_next_pcie_avail_time,lp_next_pcie_avail_time,next_pcie_avail_time; int timing_lock; struct channel_model *perf_model; };
struct ssdparams {
 int nchs,luns_per_ch,pls_per_lun,blks_per_pl;
 unsigned int slc_blks_per_pl,slc_pgs_per_blk,qlc_pgs_per_blk;
 int pg_4kb_rd_lat[4],pg_rd_lat[4],pg_wr_lat,blk_er_lat;
 int qlc_pg_4kb_rd_lat[4],qlc_pg_rd_lat[4],qlc_pg_wr_lat,qlc_blk_er_lat;
 int max_ch_xfer_size,fw_wbuf_lat0,fw_wbuf_lat1,pgsz;
};
struct ssd { struct ssdparams sp; struct ssd_channel ch[2]; struct ssd_pcie *pcie; int trace_ns_id,trace_ftl_id; };
static uint64_t __get_ioclock(struct ssd *ssd) { (void)ssd; return 1000; }
static unsigned long long fw_clock;
static unsigned long long ktime_get_ns(void) { return ++fw_clock; }
static struct nand_lun *get_lun(struct ssd *s,struct ppa *p) { return &s->ch[p->g.ch].lun[p->g.lun]; }
static struct ssd_channel *get_ch(struct ssd *s,struct ppa *p) { return &s->ch[p->g.ch]; }
static int get_cell(struct ssd *s,struct ppa *p) { (void)s;(void)p;return 0; }
static struct nand_page *get_pg(struct ssd *s,struct ppa *p) {
 static struct nand_page pg; (void)s; pg.qlc_latency_zone=p->g.pg%5; return &pg;
}
static uint64_t chmodel_request(struct channel_model *m,uint64_t ready,uint64_t len) {
 return ready+DIV_ROUND_UP(len,4096)*m->xfer_lat+13;
}
static int gc_nand_timing;
'''
MAIN = r'''
int main(int argc,char **argv) {
 struct ssd s = {0}; struct ssd_pcie pcie={0}; struct channel_model cm={23},pm={17};
 uint64_t hash=0; unsigned int i,c,l; (void)argc;
#ifdef TRACED
 fw_enabled=atoi(argv[1]);
#else
 (void)argv;
#endif
 s.pcie=&pcie; pcie.perf_model=&pm; s.trace_ns_id=1; s.trace_ftl_id=0;
 s.sp.nchs=2;s.sp.luns_per_ch=2;s.sp.pls_per_lun=1;s.sp.blks_per_pl=4;
 s.sp.slc_blks_per_pl=2;s.sp.slc_pgs_per_blk=16;s.sp.qlc_pgs_per_blk=64;
 s.sp.max_ch_xfer_size=16384;s.sp.pg_wr_lat=123;s.sp.blk_er_lat=456;
 s.sp.qlc_pg_wr_lat=789;s.sp.qlc_blk_er_lat=1234;s.sp.fw_wbuf_lat0=11;s.sp.fw_wbuf_lat1=7;
 for(i=0;i<4;i++) {s.sp.pg_4kb_rd_lat[i]=41;s.sp.pg_rd_lat[i]=83;s.sp.qlc_pg_4kb_rd_lat[i]=61+i*7;s.sp.qlc_pg_rd_lat[i]=121+i*11;}
 for(c=0;c<2;c++) s.ch[c].perf_model=&cm;
 for(i=0;i<32768;i++) {
  struct ppa a={.ppa=0}; uint64_t end; struct nand_cmd n={0};
  a.g.ch=(i/3)%2;a.g.lun=(i/7)%2;a.g.blk=(i/11)%4;a.g.pg=(i/13)%16;
  n.ppa=&a;n.type=(i%7==0)?GC_IO:USER_IO;n.cmd=(i/17)%4;n.stime=1000+(i%31)*127;
  n.xfer_size=(uint64_t[]){0,4096,8192,65536}[i%4];n.interleave_pci_dma=i%2;
  gc_nand_timing=(i/19)%2;
  if(i%3==0) end=ssd_advance_nand(&s,&n);
  else if(i%3==1) end=ssd_advance_nand_read_priority(&s,&n);
  else end=ssd_advance_nand_low_priority(&s,&n);
  hash=hash*33+end;
  if(i%5==0) hash=hash*33+ssd_advance_write_buffer(&s,1000+i,4096);
  if(i%5==1) hash=hash*33+ssd_advance_write_buffer_low_priority(&s,1000+i,8192);
  hash=hash*33+pcie.hp_next_pcie_avail_time;hash=hash*33+pcie.lp_next_pcie_avail_time;
  for(c=0;c<2;c++) { hash=hash*33+s.ch[c].hp_next_ch_avail_time;hash=hash*33+s.ch[c].lp_next_ch_avail_time;
   for(l=0;l<2;l++){hash=hash*33+s.ch[c].lun[l].hp_next_lun_avail_time;hash=hash*33+s.ch[c].lun[l].lp_next_lun_avail_time;}
  }
 }
#ifdef TRACED
 if(fw_enabled) assert(fw_event_count>32768); else assert(!fw_event_count && !fw_clock);
#endif
 printf("%llu\n",(unsigned long long)hash);return 0;
}
'''


def extract_function(text, name):
    match = re.search(r"^.*\b" + re.escape(name) + r"\([^;]*?\)\s*\{", text, re.M)
    if not match:
        raise AssertionError("Missing source function: " + name)
    start = match.start()
    brace = text.index("{", match.start())
    depth = 1
    pos = brace + 1
    # These helper bodies have no braces inside strings or comments.
    while depth:
        depth += (text[pos] == "{") - (text[pos] == "}")
        pos += 1
    return text[start:pos]


class TraceObservationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cc = shutil.which(os.environ.get("CC", "clang")) or shutil.which("cc")
        if not cls.cc:
            raise unittest.SkipTest("A C compiler is required")
        cls.base = subprocess.check_output(
            ["git", "show", BASELINE + ":ssd_die.c"], cwd=ROOT, text=True)
        cls.current = (ROOT / "ssd_die.c").read_text()

    def test_reservation_helpers_unchanged(self):
        for name in ["max3_u64", "ssd_lun_sched_start", "ssd_lun_commit_normal",
                     "ssd_lun_commit_low_priority", "ssd_lun_commit_read_priority",
                     "ssd_channel_sched_start", "ssd_channel_commit_normal",
                     "ssd_channel_commit_low_priority", "ssd_channel_commit_read_priority",
                     "ssd_pcie_sched_start", "ssd_pcie_commit_normal",
                     "ssd_pcie_commit_low_priority", "ssd_pcie_commit_read_priority"]:
            self.assertEqual(extract_function(self.base, name), extract_function(self.current, name), name)

    def test_header_and_source_extracted_scheduling(self):
        with tempfile.TemporaryDirectory(prefix="flashweave-trace-test-") as d:
            tmp = Path(d)
            (tmp / "linux").mkdir(); (tmp / "trace").mkdir()
            (tmp / "linux/types.h").write_text(TYPES)
            (tmp / "linux/tracepoint.h").write_text(TRACE_STUB)
            (tmp / "trace/define_trace.h").write_text("/* userspace test only */\n")
            stats = self.base[self.base.index("#define DIE_STAT_MAX"):self.base.index("static int die_stats_reset")]
            outputs = []
            for label, source in [("baseline", self.base), ("instrumented", self.current)]:
                body = source[source.index("static inline uint64_t ssd_pcie_xfer_time"):source.index("uint64_t ssd_next_idle_time")]
                prefix = MODEL_STUB + stats
                if label == "instrumented":
                    prefix += '\n#define TRACED\n#include "flashweave_trace.h"\n'
                    prefix += source[source.index("static atomic64_t flashweave_trace_seq"):source.index("static int gc_nand_timing")]
                src = tmp / (label + ".c")
                src.write_text(prefix + body + MAIN)
                exe = tmp / label
                subprocess.run([self.cc, "-std=gnu11", "-O1", "-Werror=format", "-Werror=implicit-function-declaration",
                                "-fsanitize=undefined,address", "-I" + str(tmp), "-I" + str(ROOT), str(src), "-o", str(exe)], check=True)
                if label == "baseline":
                    outputs.append(subprocess.check_output([str(exe), "0"], text=True))
                else:
                    outputs.extend(subprocess.check_output([str(exe), value], text=True) for value in ["0", "1"])
            self.assertEqual(outputs[0], outputs[1], "Tracing off changed reservation results")
            self.assertEqual(outputs[0], outputs[2], "Tracing on changed reservation results")

    def test_source_extracted_worker_snapshot_helpers(self):
        with tempfile.TemporaryDirectory(prefix="flashweave-worker-trace-test-") as d:
            tmp = Path(d)
            (tmp / "linux").mkdir(); (tmp / "trace").mkdir()
            (tmp / "linux/types.h").write_text(TYPES)
            (tmp / "linux/tracepoint.h").write_text(TRACE_STUB)
            (tmp / "trace/define_trace.h").write_text("/* userspace test only */\n")
            stub = r"""
#define READ_ONCE(v) (v)
#define atomic_read(p) (*(p))
#define atomic64_read(p) (*(p))
struct line_pool_stats { unsigned int free,total; };
struct conv_ftl {
 struct ssd *ssd; bool test_phase_active;
 unsigned int slc_high_watermark,slc_gc_free_thres_low,slc_target_watermark;
 int test_phase_active_reads,latency3_bg_read_priority_gate;
 int latency3_read_priority_yield_streak,latency3_read_priority_force_active;
 long long test_phase_read_priority_gate_tokens;
};
"""
            main = r"""
u64 flashweave_next_trace_id(void) { static u64 id;return ++id; }
int main(void) {
 struct ssd s={0};struct conv_ftl f={0};struct line_pool_stats pressure={5,100};
 s.trace_ns_id=1;s.trace_ftl_id=2;s.sp.pgsz=4096;f.ssd=&s;f.test_phase_active=true;
 fw_trace_worker_emit(&f,7,0,FW_DECISION_PASS_BEGIN,2,0,&pressure,1,32,-1,-1,1);
 fw_trace_worker_emit(&f,7,0,FW_DECISION_PASS_END,2,0,NULL,1,32,9,-1,1);
 assert(fw_event_count==2);return 0;
}
"""
            for family in [1, 3]:
                source = (ROOT / f"conv_ftl_latency{family}_superblock.c").read_text()
                body = extract_function(source, "test_phase_enabled") + "\n"
                body += extract_function(source, "fw_trace_worker_emit")
                src = tmp / f"worker{family}.c"
                src.write_text(MODEL_STUB + stub + '#include "flashweave_trace.h"\n' + body + main)
                exe = tmp / f"worker{family}"
                subprocess.run([self.cc, "-std=gnu11", "-O1", "-Werror=format",
                                "-Werror=implicit-function-declaration", "-fsanitize=undefined,address",
                                "-I" + str(tmp), "-I" + str(ROOT), str(src), "-o", str(exe)], check=True)
                subprocess.run([str(exe)], check=True)

    def test_trace_header_structure(self):
        header = (ROOT / "flashweave_trace.h").read_text()
        self.assertEqual(header.count("TRACE_EVENT("), 3)
        self.assertIn("#define TRACE_INCLUDE_PATH .", header)
        self.assertIn("#define TRACE_SYSTEM flashweave", header)
        self.assertEqual(self.current.count("#define CREATE_TRACE_POINTS"), 1)
        for name in ["nand_schedule", "resource_schedule", "worker_decision"]:
            fields = re.search(r"struct fw_" + name + r" \{(.*?)\};", header, re.S).group(1)
            keys = re.findall(r"\b(\w+);", fields)
            event = header.split("TRACE_EVENT(" + name + ",", 1)[1].split("\n);", 1)[0]
            for key in keys:
                self.assertIn("__entry->" + key + " = v->" + key + ";", event)
                self.assertIn(key + "=%", event)


if __name__ == "__main__":
    unittest.main(verbosity=2)
