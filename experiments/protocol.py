#!/usr/bin/env python3
"""Repository-native reproducible ablations. Preview never opens a device."""
import argparse,csv,json,os,platform,re,shutil,subprocess,sys,time
from contextlib import contextmanager
from pathlib import Path
from provenance import PROTOCOL_VERSION, digest, freeze_source
VARIANTS={"A":"die_latency1_norp_sb","B":"die_latency1_qlc_all_norp_sb",
          "C":"die_latency3_norp_sb","D":"die_latency3_qlc_all_norp_sb"}
FLAGS=("-DNVMEV_LATENCY3_REQUEUE_DELAY_US=0U "
 "-DNVMEV_LATENCY3_FORCE_AFTER_YIELDS=8U -DNVMEV_LATENCY3_FORCE_CATCHUP_MAX=8U "
 "-DNVMEV_LATENCY3_READ_WINDOW_GATE_TOKENS=8U "
 "-DNVMEV_TEST_PHASE_READ_REQ_LATENCY_STATS=1 -DNVMEV_TEST_PHASE_READ_PRIORITY_DIAG=1")
HERE=Path(__file__).resolve().parent
TRACE_STOP_WAIT_SECONDS=300
TRACE_STOP_CLIENT_TIMEOUT_SECONDS=310
KERNEL_FAULT = re.compile(r"BUG:|Oops:|kernel BUG|KASAN:|UBSAN:|general protection fault|WARNING: CPU:|blocked for more than [0-9]|watchdog:.*(?:soft|hard) LOCKUP", re.I)
def kernel_fault_lines(lines):
    from itertools import islice
    return list(islice((line.strip() for line in lines if KERNEL_FAULT.search(line)),20))
def fail(s): raise SystemExit("ERROR: "+s)
def trace_finalization(out):
    """A stop-client exit alone cannot certify an asynchronously written trace."""
    result=Path(out)/"trace/result.json"
    if not result.is_file():
        return {"finalized":False,"reason":"atomic capture result is absent; trace files may still change"}
    try:
        capture=json.loads(result.read_text())
        valid=(capture.get("status") in ("captured","capture_failed") and
               isinstance(capture.get("incomplete"),bool) and
               isinstance(capture.get("finished_ns"),int))
        return {"finalized":valid,"result_status":capture.get("status"),
                "incomplete":capture.get("incomplete"),"finished_ns":capture.get("finished_ns"),
                "problems":capture.get("problems",[])}
    except (OSError,ValueError) as exc:
        return {"finalized":False,"reason":"capture result could not be read: "+str(exc)}
def command(a,**kw): return subprocess.check_output(a,text=True,**kw).strip()
def once(s,a,b):
    if s.count(a)!=1: fail("unsupported current-source anchor: "+a[:90])
    return s.replace(a,b,1)
def safe_path(p):
    p=Path(p).resolve()
    if not re.fullmatch(r"[A-Za-z0-9_/.,+-]+",str(p)): fail("fio exec hooks require paths without spaces/metacharacters")
    return p
def cpu_ranges(value,label):
    if not re.fullmatch(r"[0-9]+(?:-[0-9]+)?(?:,[0-9]+(?:-[0-9]+)?)*",value or ""):
        fail(label+" must contain only CPU numbers, commas and ascending ranges")
    result=[]
    for item in value.split(","):
        pair=item.split("-");start=int(pair[0]);end=int(pair[-1])
        if end<start or end-start>65535: fail("invalid or oversized "+label+" range")
        result.extend(range(start,end+1))
    if len(set(result))!=len(result): fail("duplicate CPUs in "+label)
    return result
def machine_arguments(a,online_text=None):
    if not re.fullmatch(r"[0-9]+(?:,[0-9]+)+",a.cpus or ""):
        fail("--cpus requires comma-separated integer dispatcher and worker CPUs")
    emulated=[int(value) for value in a.cpus.split(",")]
    if len(set(emulated))!=len(emulated): fail("duplicate dispatcher/worker CPUs")
    host=cpu_ranges(a.host_cpus,"--host-cpus")
    if online_text is None: online_text=Path("/sys/devices/system/cpu/online").read_text().strip()
    online=set(cpu_ranges(online_text,"online CPU list"))
    if not set(emulated+host)<=online: fail("requested CPU is not online")
    if set(emulated)&set(host): fail("host fio CPUs and emulator CPUs must be disjoint")
    match=re.fullmatch(r"(0[xX][0-9a-fA-F]+|0|[1-9][0-9]*)([kKmMgG]?)",a.memmap_start or "")
    if not match: fail("--memmap-start must be decimal/0x hexadecimal with optional K/M/G suffix")
    number=int(match[1],16 if match[1].lower().startswith("0x") else 10)
    reservation_start=number*{"":1,"k":1024,"m":1024**2,"g":1024**3}[match[2].lower()]
    if reservation_start<=0: fail("--memmap-start must be positive")
    return {"online":sorted(online),"emulator_dispatcher":emulated[0],
            "emulator_workers":emulated[1:],"host":host,"memmap_start_bytes":reservation_start,
            "reservation_gate":"MANUAL: operator must verify this entire reserved range in /proc/iomem; not automatically certified"}
def executor_hashes(directory):
    return {p.name:digest(p) for p in sorted(Path(directory).iterdir())
            if p.is_file() and p.suffix in (".py",".sh",".csv")
            and not p.name.startswith("test_") and p.name!="host_config.sh"}
def verify_executors(record):
    hashes=record.get("executor_hashes")
    if not hashes: fail("executor identity missing; prepare again")
    for name,want in hashes.items():
        p=HERE/name
        if name=="phase_hook.py" and not p.is_file(): p=HERE/"protocol_hook.py"
        if not p.is_file() or digest(p)!=want: fail("experiment executor changed: "+name)
def verify_build(ws,info,variant,module):
    p=ws/"protocol_build.json"
    if not p.is_file(): fail("protocol_build.json absent; use experiments/build_all.sh")
    record=json.loads(p.read_text())
    if record.get("protocol_inputs_sha256")!=digest(ws/"protocol_inputs.json"):
        fail("build manifest is not bound to these prepared inputs")
    if record.get("build_flags")!=info["build_flags"] or record.get("source_commit")!=info["source_commit"]:
        fail("build manifest source/flags mismatch")
    if record.get("kernel_release")!=platform.release(): fail("build kernel differs from running kernel")
    entry=record.get("modules",{}).get(variant,{})
    if not module.is_file() or entry.get("sha256")!=digest(module): fail("selected module differs from recorded build")
    return record
@contextmanager
def execution_lease():
    import fcntl
    lock=Path("/run/lock/flashweave-experiment.lock")
    lock.parent.mkdir(parents=True,exist_ok=True)
    with lock.open("a+") as stream:
        try: fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: fail("another FlashWeave experiment holds the global module lease")
        stream.seek(0);stream.truncate();stream.write(str(os.getpid())+"\n");stream.flush()
        try: yield
        finally: fcntl.flock(stream,fcntl.LOCK_UN)
def prepare(a):
    repo=safe_path(a.repo);ws=safe_path(a.workspace)
    try: source=freeze_source(repo,a.allow_dirty_preview)
    except RuntimeError as exc: fail(str(exc))
    native=repo/"experiments"
    if not (native/"protocol.py").is_file() or digest(native/"protocol.py")!=digest(__file__):
        fail("run the protocol.py version belonging to --repo")
    if not re.fullmatch(r"nvme[0-9]+n[0-9]+",a.device_name): fail("whole NVMe namespace name required")
    subprocess.run([sys.executable,str(repo/"tools/prepare_workspace.py"),"--output",str(ws)],check=True)
    ev=ws/"evaluation";mod=ws/"nvmevirt_DA"
    p=mod/"ssd_config.h";p.write_text(once(p.read_text(),"#define NAND_CHANNELS (4)","#define NAND_CHANNELS (2)"))
    p=ev/"commonvariables.sh";p.write_text(once(p.read_text(),'DATA_NAME="nvme4n1"','DATA_NAME="'+a.device_name+'"'))
    for name,target in [("phase_hook.py","protocol_hook.py"),("fio_coordinator.py","fio_coordinator.py"),
                        ("provenance.py","provenance.py")]:
        shutil.copy2(native/name,ev/target)
    loader="""#!/bin/bash
set -euo pipefail
: "${FW_MEMMAP_START:?set actual reserved physical start}"
: "${FW_CPUS:?set actual dispatcher and worker CPUs}"
: "${FW_PROTOCOL_CASE_DIR:?}"
: "${FW_PROTOCOL_VARIANT:?}"
insmod ./nvmev_on.ko memmap_start="$FW_MEMMAP_START" memmap_size=65537M cpus="$FW_CPUS" gc_nand_timing=1 test_phase_recent_write_guard=1 test_phase_guard_read_reqs=256
printf 'normal\\n' > /proc/nvmev/latency_profile
python3 ./protocol_hook.py preinit "$FW_PROTOCOL_CASE_DIR/preinit" "$FW_PROTOCOL_VARIANT" """+a.device_name+"\n"
    (ev/"nvmevstart_on.sh").write_text(loader);(ev/"nvmevstart_on.sh").chmod(0o755)
    p=ev/"fio_fragment_die_latency_mixed.sh";s=p.read_text()
    s=once(s,'echo "randrepeat=0"','echo "randrepeat=1"\n        echo "randseed=$FW_PROTOCOL_SEED"')
    s=once(s,"--output-format=json; then","--output-format=json+; then")
    s=once(s,'if sudo fio "$fio_job_tmp"',
           'if python3 "$SCRIPT_DIR/fio_coordinator.py" "$fio_job_tmp"')
    for kind in ("read","write"):
        anchor='echo "write_lat_log=${log_prefix}_'+kind+'"'
        s=once(s,anchor,anchor+'\n        echo "write_hist_log=${log_prefix}_'+kind+'"\n        echo "log_hist_msec=1000"\n        echo "log_hist_coarseness=0"')
    s=once(s,'echo "[measure_reads]"','echo "[measure_reads]"\n        echo "stonewall"')
    s=once(s,'if [[ "$FIO_DRY_RUN" == "1" ]]; then\n        rm -f "$fio_job_tmp"\n        return 0\n    fi',
           'if [[ "$FIO_DRY_RUN" == "1" ]]; then\n        python3 "$SCRIPT_DIR/fio_coordinator.py" "$fio_job_tmp" --output="$out_json" --output-format=json+ --plan-only\n        rm -f "$fio_job_tmp"\n        return 0\n    fi')
    s=once(s,'if [[ ! -e "$path" ]]; then\n        echo "[module_param] skip $label: $path not present"\n        return 0\n    fi',
           'if [[ ! -e "$path" ]]; then\n        die "required module control absent: $path"\n    fi')
    s=once(s,'echo "[module_param] failed to set $label=$value path=$path" >&2',
           'die "failed module control: $label=$value path=$path"')
    s=once(s,'[[ -f "$stats_file" ]] || return 0','[[ -f "$stats_file" ]] || die "compile flags snapshot missing: $stats_file"')
    s=once(s,'echo "[fio-drain] unable to read $FIO_TEST_PHASE_STATS_PATH; skipping drain wait" >&2\n            rm -f "$tmp_stats"\n            return 0',
           'echo "missing_stats" > "$FIO_OUTPUT_DIR/stationary_status.txt"\n            rm -f "$tmp_stats"\n            return 1')
    s=once(s,'echo "[fio-drain] stable after ${elapsed}s"',
           'echo "[fio-drain] stable after ${elapsed}s"\n            echo "statistical_stationarity_only ${elapsed}" > "$FIO_OUTPUT_DIR/stationary_status.txt"')
    anchor='        active_reads="$(numeric_stat_value active_reads "$tmp_stats")"'
    strict='        for required_key in active_reads active_overwrites active_bg_ops $FIO_DRAIN_STABLE_KEYS; do\n            grep -Eq "^${required_key}[[:space:]]+[0-9]+$" "$tmp_stats" || die "required drain key missing: $required_key"\n        done\n'
    s=once(s,anchor,strict+anchor)
    s=once(s,'echo "[fio-drain] timed out after ${FIO_DRAIN_TIMEOUT_SEC}s; capturing post-drain stats anyway" >&2\n    rm -f "$tmp_stats"\n    return 0',
           'echo "timeout" > "$FIO_OUTPUT_DIR/stationary_status.txt"\n    rm -f "$tmp_stats"\n    return 1')
    s=once(s,'    if [[ "$FIO_DRAIN_AFTER_MEASURE" == "1" ]]; then',
           '    printf "%s\\n" "$rc" > "$FW_PROTOCOL_CASE_DIR/workload_exit_code.txt"\n    if [[ "$FIO_DRAIN_AFTER_MEASURE" == "1" ]]; then')
    anchor='    rm -f "$fio_job_tmp"\n    sudo rmmod nvmev'
    stop='    if [[ "${FW_TRACE_MODE:-off}" == diag ]]; then\n        local fw_trace_rc=0\n        if python3 "$SCRIPT_DIR/trace_capture.py" stop --out "$FW_PROTOCOL_CASE_DIR/trace" > "$FW_PROTOCOL_CASE_DIR/trace_stop.stdout.txt" 2> "$FW_PROTOCOL_CASE_DIR/trace_stop.stderr.txt"; then\n            fw_trace_rc=0\n        else\n            fw_trace_rc=$?\n        fi\n        printf "%s\\n" "$fw_trace_rc" > "$FW_PROTOCOL_CASE_DIR/trace_stop_exit_code.txt"\n        if (( fw_trace_rc != 0 )); then return "$fw_trace_rc"; fi\n    fi\n'
    s=once(s,anchor,'    python3 "$SCRIPT_DIR/protocol_hook.py" final "$out_dir/final" "$variant" "$DATA_NAME"\n'+stop+anchor)
    s=once(s,'mkdir -p "$RESULT_FOLDER" "$FIO_OUTPUT_DIR"',
           ': "${FW_PROTOCOL_SEED:?use protocol.py with an explicit seed}"\n: "${FW_PROTOCOL_VARIANT:?}"\nmkdir -p "$RESULT_FOLDER" "$FIO_OUTPUT_DIR"')
    p.write_text(s)
    for p in [p,ev/"nvmevstart_on.sh"]: subprocess.run(["bash","-n",str(p)],check=True)
    (ws/"protocol_build_flags.txt").write_text(FLAGS+"\n")
    final_source=freeze_source(repo,a.allow_dirty_preview)
    if final_source["base_commit"]!=source["base_commit"]: fail("HEAD changed during preparation")
    if source["source_dirty"]:
        (ws/"source.patch").write_text(command(["git","diff","--binary","HEAD"],cwd=repo)+"\n")
    record={**source,"purpose":"protocol staging; no kernel run certified",
      "controller_sha256":digest(__file__),
      "executor_hashes":executor_hashes(native),
      "device_name":a.device_name,"geometry":"2 channels,2 LUNs/channel,1 plane; ratio1:15",
      "reserved_memmap_size":"65537M; host must actually reserve this range",
      "model_drain_available":False,"build_flags":FLAGS,
      "fio_execution":"synchronous coordinator stages, no fio shell hook execution",
      "files_sha256":{str(p.relative_to(ws)):digest(p) for p in sorted(ws.rglob("*"))
                      if p.is_file() and p.name!="protocol_inputs.json"}}
    (ws/"protocol_inputs.json").write_text(json.dumps(record,indent=2)+"\n")
    print("Prepared",ws)
def environment(ws,out,seed,variant,profile,trace_mode="off",trace_max_bytes=8*1024**3):
    env={k:v for k,v in os.environ.items() if not k.startswith(("FIO_","SQLITE_","FW_"))}
    env.update({
     "FW_PROTOCOL_SEED":str(seed),"FW_PROTOCOL_VARIANT":variant,"FW_PROTOCOL_CASE_DIR":str(out),
     "FW_TRACE_MODE":trace_mode,
     "FW_TRACE_MAX_BYTES":str(trace_max_bytes),
     "VARIANTS":variant,"FIO_REBUILD_DIE_MODULES":"0","FIO_ACCESS_DIST_LIST":"zipf",
     "FIO_RW_RATIOS":"10:1","FIO_REGION_SIZE":"8G","FIO_INIT_WRITE_BYTES":"8G",
     "FIO_INIT_CHUNK_SIZE":"512M","FIO_INIT_CHUNK_COUNT":"0","FIO_INIT_PREWARM_BYTES_PER_CHUNK":"5x",
     "FIO_INIT_PREWARM_GUARD":"1","FIO_INIT_ADVANCE_HEAT_EPOCH_EACH_CHUNK":"1",
     "FIO_PREWARM_SEQ_BYTES":"0","FIO_PREWARM_RANDOM_BYTES":"0",
     "FIO_INIT_PREWARM_JOBS":"1","FIO_INIT_PREWARM_IODEPTH":"32",
     "FIO_MEASURE_READ_BYTES":"5G","FIO_MEASURE_WRITE_BYTES":"512M",
     "FIO_MEASURE_WRITE_OFFSET":"8G","FIO_MEASURE_WRITE_REGION_SIZE":"512M",
     "FIO_MEASURE_JOBS":"1","FIO_MEASURE_READ_JOBS":"1","FIO_MEASURE_WRITE_JOBS":"1",
     "FIO_MEASURE_IODEPTH":"32","FIO_MEASURE_READ_IODEPTH":"32","FIO_MEASURE_WRITE_IODEPTH":"32",
     "FIO_MEASURE_RATE":"","FIO_MEASURE_RATE_IOPS":"","FIO_ZIPF_ALPHA":"0.75",
     "FIO_GC_NAND_TIMING":"1","FIO_TEST_PHASE_RECENT_WRITE_GUARD":"1",
     "FIO_TEST_PHASE_GUARD_READ_REQS":"256","FIO_STRICT_COMPILE_FLAGS":"1",
     "FIO_DRAIN_AFTER_MEASURE":"1","FIO_DRAIN_MIN_SEC":"60","FIO_DRAIN_TIMEOUT_SEC":"600",
     "FIO_DRAIN_POLL_SEC":"5","FIO_DRAIN_STABLE_POLLS":"3","FIO_OUTPUT_DIR":str(out/"raw")})
    if profile=="pressure":
        env.update({"FIO_RW_RATIOS":"8:2","FIO_MEASURE_READ_BYTES":"8G",
                    "FIO_MEASURE_WRITE_BYTES":"2G","FIO_MEASURE_WRITE_REGION_SIZE":"8G"})
    return env
def run(a):
    if a.execute:
        if platform.system()!="Linux" or os.geteuid()!=0: fail("execution requires Linux root")
        with execution_lease(): return run_locked(a)
    return run_locked(a)
def run_locked(a):
    ws=safe_path(a.workspace);out=safe_path(a.out)
    if out.exists(): fail("output exists; use a new attempt directory")
    info=json.loads((ws/"protocol_inputs.json").read_text());variant=VARIANTS[a.cell]
    verify_executors(info)
    module=ws/"nvmevirt_DA"/("nvmev_"+variant+".ko")
    binding=None
    if a.execute:
        if not info.get("allow_execute") or info.get("source_dirty") or info.get("dirty_preview_only"):
            fail("dirty/development staging is preview-only; prepare from a clean committed revision")
        if info.get("controller_sha256")!=digest(__file__): fail("controller version changed; prepare again")
        if platform.system()!="Linux" or os.geteuid()!=0: fail("execution requires Linux root")
        if Path("/sys/module/nvmev").exists(): fail("nvmev already loaded; inspect/unload deliberately")
        if not a.memmap_start or not a.cpus or not a.host_cpus: fail("machine reservation and CPU arguments required")
        binding=machine_arguments(a)
        if command(["fio","--version"])!="fio-3.16": fail("this first protocol pins fio-3.16 semantics")
        if not module.is_file(): fail("selected module not built")
        verify_build(ws,info,variant,module)
        for relative,want in info["files_sha256"].items():
            if not (ws/relative).is_file() or digest(ws/relative)!=want:
                fail("staged input changed after preparation: "+relative)
    out.mkdir(parents=True)
    limit=getattr(a,"trace_max_bytes",8*1024**3)
    if limit<1: fail("trace byte limit must be positive")
    env=environment(ws,out,a.seed,variant,a.profile,a.trace_mode,limit);env["FIO_DRY_RUN"]="0" if a.execute else "1"
    env["FW_PROTOCOL_FIO"]=str(Path(shutil.which("fio")).resolve()) if a.execute else "/usr/bin/fio"
    env["FW_MEMMAP_START"]=a.memmap_start or "UNSET";env["FW_CPUS"]=a.cpus or "UNSET"
    manifest={"status":"started","execute":a.execute,"seed":a.seed,"cell":a.cell,"variant":variant,
      "profile":a.profile,"source_commit":info["source_commit"],"base_commit":info["base_commit"],
      "source_dirty":info["source_dirty"],"protocol_version":info["protocol_version"],
      "executor_hashes":info["executor_hashes"],
      "trace_mode":a.trace_mode,"dataset_kind":"diagnostic_trace_only" if a.trace_mode=="diag" else a.profile,
      "trace_max_bytes":limit if a.trace_mode=="diag" else None,
      "time_started_ns":time.time_ns(),
      "workspace_manifest_sha256":digest(ws/"protocol_inputs.json"),"kernel":platform.release(),
      "host_cpus":a.host_cpus,"build_flags":info["build_flags"],
      "machine_argument_validation":binding,
      "environment":{k:v for k,v in env.items() if k.startswith(("FIO_","FW_"))},
      "module_sha256":digest(module) if module.is_file() else None,
      "scope":"scheduler-family comparison; statistical stationarity is not full model drain"}
    if a.execute:
        manifest["fio_binary"]={"path":env["FW_PROTOCOL_FIO"],"sha256":digest(env["FW_PROTOCOL_FIO"]),
                                "version":command([env["FW_PROTOCOL_FIO"],"--version"])}
    (out/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    if a.execute:
        for name,cmd in [("cpu",["lscpu"]),("numa",["numactl","--hardware"]),
                         ("compiler",["gcc","--version"])]:
            try:
                r=subprocess.run(cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
                (out/(name+".txt")).write_text(r.stdout)
            except OSError as exc: (out/(name+".txt")).write_text("unavailable: "+str(exc))
        for name,path in [("cmdline","/proc/cmdline"),("iomem","/proc/iomem"),
                          ("meminfo","/proc/meminfo"),("kernel_config","/boot/config-"+platform.release())]:
            if Path(path).is_file(): shutil.copyfile(path,out/(name+".txt"))
    shutil.copy2(ws/"protocol_inputs.json",out/"protocol_inputs.json")
    if a.execute:
        shutil.copy2(ws/"protocol_build.json",out/"protocol_build.json")
        manifest["build_manifest_sha256"]=digest(ws/"protocol_build.json")
    logs=None;kernel_file=None;rc=1
    try:
        if a.execute:
            kernel_file=(out/"kernel_scoped.log").open("w")
            logs=subprocess.Popen(["dmesg","--follow-new","--time-format","raw"],
                    stdout=kernel_file,stderr=subprocess.STDOUT,text=True)
            time.sleep(.2)
            if logs.poll() is not None: fail("scoped kernel capture failed")
        cmd=["bash",str(ws/"evaluation/fio_fragment_die_latency_mixed.sh")]
        if a.execute: cmd=["taskset","-c",a.host_cpus]+cmd
        manifest["command"]=cmd
        with (out/"console.log").open("w") as stream:
            rc=subprocess.run(cmd,cwd=ws/"evaluation",env=env,
                  stdout=stream,stderr=subprocess.STDOUT).returncode
    finally:
        if a.execute and rc!=0 and a.trace_mode=="diag" and (out/"trace").exists():
            try:
                with (out/"trace_cleanup.txt").open("w") as stream:
                    stopped=subprocess.run([sys.executable,str(ws/"evaluation/trace_capture.py"),
                      "stop","--out",str(out/"trace"),"--timeout",str(TRACE_STOP_WAIT_SECONDS)],
                      stdout=stream,stderr=subprocess.STDOUT,timeout=TRACE_STOP_CLIENT_TIMEOUT_SECONDS)
                manifest["trace_cleanup_returncode"]=stopped.returncode
            except Exception as exc: manifest["trace_cleanup_error"]=str(exc)
        if a.execute and rc!=0:
            try:
                with (out/"failure_snapshot.stdout.txt").open("w") as diagnostic_out, (out/"failure_snapshot.stderr.txt").open("w") as diagnostic_err:
                    diagnostic=subprocess.run([sys.executable,str(ws/"evaluation/protocol_hook.py"),
                      "failure",str(out/"failure_snapshot"),variant,info["device_name"]],
                      cwd=ws/"evaluation",stdout=diagnostic_out,stderr=diagnostic_err,timeout=15)
                manifest["failure_snapshot_returncode"]=diagnostic.returncode
            except Exception as exc:
                manifest["failure_snapshot_error"]=str(exc)
            manifest["failure_cleanup_scope"]="best-effort read-only snapshot; no automatic module unload; original failing runner code retained"
        if logs is not None:
            logs.terminate()
            try: logs.wait(timeout=5)
            except subprocess.TimeoutExpired: logs.kill();logs.wait()
            kernel_file.close()
        pending_trace=False
        if a.execute and a.trace_mode=="diag":
            manifest["trace_finalization"]=trace_finalization(out)
            pending_trace=not manifest["trace_finalization"]["finalized"]
            if pending_trace:
                manifest["artifact_hash_excluded_prefixes"]=["trace/"]
                manifest["trace_recovery_required"]="capture is not finalized; preserve module and session; do not continue batch"
                if rc==0: rc=1
        manifest.update({"time_finished_ns":time.time_ns(),"runner_exit_code":rc,
                         "status":("runner_ok" if a.execute else "static_preview_ok") if rc==0 else "failed"})
        for key,name in [("workload_validation_exit_code","workload_exit_code.txt"),
                         ("trace_stop_exit_code","trace_stop_exit_code.txt")]:
            value=out/name
            if value.is_file(): manifest[key]=int(value.read_text().strip())
        manifest["artifacts_sha256"]={str(p.relative_to(out)):digest(p) for p in sorted(out.rglob("*"))
                                     if p.is_file() and p.name!="manifest.json" and
                                     not (pending_trace and p.relative_to(out).parts[0]=="trace")}
        (out/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    print("Execution" if a.execute else "Preview",out,"exit",rc)
    if rc: fail("preserve failed run and investigate before the next cell")
def check(a):
    run=Path(a.run);m=json.loads((run/"manifest.json").read_text())
    if not m["execute"]: fail("preview is not a measured run")
    if m["runner_exit_code"]!=0: fail("runner failed")
    if m.get("trace_mode")=="diag" and (not m.get("trace_finalization",{}).get("finalized") or
        m["trace_finalization"].get("result_status")!="captured"):
        fail("diagnostic capture did not finalize successfully")
    verify_executors(m)
    if m.get("source_dirty") or not m.get("source_commit"): fail("measured run lacks committed source identity")
    if digest(run/"protocol_build.json")!=m.get("build_manifest_sha256"): fail("build identity changed")
    for relative,want in m["artifacts_sha256"].items():
        if not (run/relative).is_file() or digest(run/relative)!=want: fail("artifact changed: "+relative)
    kernel_log=run/"kernel_scoped.log"
    if not kernel_log.is_file(): fail("scoped kernel diagnostics missing")
    with kernel_log.open(errors="replace") as stream:
        faults=kernel_fault_lines(stream)
    if faults: fail("kernel fault evidence; preserve run: "+" | ".join(faults))
    if not (run/"raw/stationary_status.txt").read_text().startswith("statistical_stationarity_only "):
        fail("statistical stationarity did not complete")
    files=list((run/"raw").rglob("fio_mixed_*.json"))
    if len(files)!=1: fail("expect exactly one JSON+ output")
    data=json.loads(files[0].read_text())
    if any(j.get("error",0) for j in data["jobs"]): fail("fio job error")
    for name,direction,key in [("measure_reads","read","FIO_MEASURE_READ_BYTES"),
                               ("measure_writes","write","FIO_MEASURE_WRITE_BYTES")]:
        jobs=[j for j in data["jobs"] if j["jobname"]==name]
        if len(jobs)!=1: fail("clone aggregation unsupported in first protocol")
        result=jobs[0][direction];lat=result.get("clat_ns",{});bins=lat.get("bins")
        if not bins: fail("JSON+ completion bins missing")
        n=int(result["total_ios"])
        if sum(int(v) for v in bins.values())!=n: fail("histogram sample conservation failed")
        if "N" in lat and int(lat["N"])!=n: fail("clat N differs")
        match=re.fullmatch(r"([0-9]+)([GM])",m["environment"][key])
        expected=int(match[1])*{"G":1024**3,"M":1024**2}[match[2]]
        if int(result["io_bytes"])!=expected or n*4096!=expected: fail("I/O volume conservation failed")
    baseline_files=list((run/"raw").rglob("*.baseline/snapshot.json"))
    final_files=list((run/"raw").rglob("final/snapshot.json"))
    if len(baseline_files)!=1: fail("measurement baseline missing")
    if len(final_files)!=1: fail("final snapshot missing")
    for snapshot in [run/"preinit/snapshot.json",baseline_files[0],final_files[0]]:
        if json.loads(snapshot.read_text()).get("status")!="ok": fail("snapshot validation failed: "+str(snapshot))
    coordinator=list((run/"raw").rglob("*.stages/status.json"))
    if len(coordinator)!=1: fail("coordinator execution status missing")
    state=json.loads(coordinator[0].read_text())
    if state.get("status")!="stages_completed" or not state.get("baseline_succeeded"):
        fail("synchronous fio stages/baseline did not complete")
    preinit=json.loads((run/"preinit/snapshot.json").read_text())
    if preinit.get("selected_module_sha256")!=m["module_sha256"]: fail("loaded module identity evidence differs")
    print("PASS: volumes/errors/histogram counts/snapshots/statistical stationarity")
    print("NOT CERTIFIED: full model drain, causal attribution or non-preemption")
def matrix(a):
    with Path(a.out).open("x",newline="") as f:
        w=csv.writer(f);w.writerow(["block","seed","position","cell","variant","dataset"])
        for block,(seed,order) in enumerate(zip([101,202,303,404,505],
                                                ["ABDC","BCAD","CDBA","DACB","ABDC"]),1):
            for pos,cell in enumerate(order,1): w.writerow([block,seed,pos,cell,VARIANTS[cell],"formal_10to1"])
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--version",action="version",version=PROTOCOL_VERSION)
    subs=p.add_subparsers(dest="action",required=True)
    q=subs.add_parser("prepare");q.add_argument("--repo",required=True);q.add_argument("--workspace",required=True)
    q.add_argument("--device-name",required=True)
    q.add_argument("--allow-dirty-preview",action="store_true",help="development only: resulting workspace can never execute device I/O")
    q.set_defaults(func=prepare)
    for name in ["preview","run"]:
        q=subs.add_parser(name);q.add_argument("--workspace",required=True);q.add_argument("--out",required=True)
        q.add_argument("--cell",choices=VARIANTS,required=True);q.add_argument("--seed",type=int,required=True)
        q.add_argument("--profile",choices=["formal","pressure"],default="formal")
        q.add_argument("--trace-mode",choices=["off","diag"],default="off",help="diag is a separate dataset; formal defaults off")
        q.add_argument("--trace-max-bytes",type=int,default=8*1024**3,help="diagnostic trace size cap; exceeding it fails collection")
        q.add_argument("--memmap-start");q.add_argument("--cpus");q.add_argument("--host-cpus")
        q.set_defaults(func=run,execute=(name=="run"))
    q=subs.add_parser("check");q.add_argument("--run",required=True);q.set_defaults(func=check)
    q=subs.add_parser("matrix");q.add_argument("--out",required=True);q.set_defaults(func=matrix)
    a=p.parse_args()
    if hasattr(a,"seed") and a.seed<1: fail("seed must be positive")
    a.func(a)
if __name__=="__main__": main()
