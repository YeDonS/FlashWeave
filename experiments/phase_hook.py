#!/usr/bin/env python3
"""Explicit runtime hook; pure validation helpers below never access sysfs.

Interfaces are checked against runtime readbacks; protocol_inputs.json freezes
the source commit, helper versions and staged inputs for each workspace:
ssd_die.c:34--110, main.c:88--102/676--680, and V1/V3 stats show functions.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

MIB = 1024**2
HOST_PAGE_BYTES = 4096
# The pinned Identify Namespace defaults to lbaf[0].ds=9 and flbas=0.
# This is independent of both 4 KiB FTL pages and sysfs's 512-byte size unit.
NVME_LBA_BYTES = 512
SOURCE_PBA_PERCENT = 107  # OP_AREA_PERCENT=0.07 in the selected 970PRO branch.
EXPECTED_MEMMAP_BYTES = 65537 * MIB
EXPECTED_GEOMETRY = {
    "die_count": 4, "slc_pages_per_block": 256, "qlc_pages_per_block": 1024,
    "slc_pages_per_superblock": 1024, "qlc_pages_per_superblock": 4096,
    "slc_total_lines": 1024, "qlc_total_lines": 3840,
}
MECHANISMS = {
    "die_latency1_norp_sb": ("conv_ftl_latency1_superblock", "v1_global_worker", 0),
    "die_latency1_qlc_all_norp_sb": ("conv_ftl_latency1_superblock", "v1_global_worker", 1),
    "die_latency3_norp_sb": ("conv_ftl_latency3_superblock", "v3_read_priority_worker", 0),
    "die_latency3_qlc_all_norp_sb": ("conv_ftl_latency3_superblock", "v3_read_priority_worker", 1),
}
OFF_FLAGS = (
    "compile_read_repromotion_enabled", "compile_die_batched_repromotion_enabled",
    "compile_qlc_rebalance_enabled", "compile_test_phase_repromotion_enabled",
    "compile_test_phase_qlc_rebalance_enabled",
)
BG_FIELDS = (
    "busy_ns", "read_ns", "write_ns", "erase_ns", "read_ops", "write_ops", "erase_ops",
    "read_prio_bypass_ops", "read_prio_bypass_ns", "read_prio_ch_bypass_ops",
    "read_prio_ch_bypass_ns", "read_prio_pcie_bypass_ops", "read_prio_pcie_bypass_ns",
    "lp_host_write_ops", "lp_host_write_ns",
)


class ProtocolError(RuntimeError):
    pass


def fail(message):
    raise ProtocolError(message)


def key_values(value):
    """Retain exact string fields as well as counters from two-column debugfs."""
    result = {}
    for line in value.splitlines():
        fields = line.split()
        if len(fields) == 2 and not fields[0].startswith("#"):
            if fields[0] in result:
                fail("duplicate debugfs field: " + fields[0])
            result[fields[0]] = fields[1]
    return result


def numeric_stats(value):
    result = {}
    for key, raw in key_values(value).items():
        try:
            result[key] = int(raw)
        except ValueError:
            pass
    return result


def memory_bytes(value):
    # param_get_ulong emits decimal bytes; suffix support makes fixtures explicit.
    match = re.fullmatch(r"(0[xX][0-9a-fA-F]+|[0-9]+)([KMGTPEkmgtpe]?)", value.strip())
    if not match:
        fail("invalid numeric memory parameter: " + repr(value))
    number = int(match[1], 16 if match[1].lower().startswith("0x") else 10)
    shift = "KMGTPE".find(match[2].upper()) + 1 if match[2] else 0
    return number * 1024**shift


def verify_mechanism(raw, variant, label):
    if variant not in MECHANISMS:
        fail("unsupported protocol variant: " + variant)
    source, scheduler, placement = MECHANISMS[variant]
    expected = {
        "mechanism_source": source, "mechanism_scheduler": scheduler,
        "slc_migration_core": "page_level_cold_filter",
        "test_phase_repromote_policy": "blocked",
        "compile_qlc_hotcold_enabled": str(placement),
        "compile_slc_gc_requires_complete_migration": "1",
        **{key: "0" for key in OFF_FLAGS},
    }
    actual = key_values(raw)
    for key, want in expected.items():
        if actual.get(key) != want:
            fail(f"{label}: {key}={actual.get(key)!r}; expected {want!r} for {variant}")
    return {key: actual[key] for key in expected}


def verify_geometry(raw, controls, block):
    values = numeric_stats(raw)
    for key, want in EXPECTED_GEOMETRY.items():
        if values.get(key) != want:
            fail(f"superblock_stats: {key}={values.get(key)!r}; expected {want}")
    reserved = memory_bytes(controls["memmap_size"])
    start = memory_bytes(controls["memmap_start"])
    if reserved != EXPECTED_MEMMAP_BYTES:
        fail(f"memmap_size={reserved} bytes; expected 65537 MiB ({EXPECTED_MEMMAP_BYTES})")
    if start <= 0 or start % HOST_PAGE_BYTES:
        fail("memmap_start must be a positive host-page-aligned physical address")
    namespace_bytes = int(block["sectors_512"]) * 512
    logical_block = int(block["logical_block_bytes"])
    physical_block = int(block["physical_block_bytes"])
    if logical_block <= 0 or physical_block <= 0 or physical_block % logical_block:
        fail("invalid logical/physical block-size readback")
    if logical_block != NVME_LBA_BYTES:
        fail(f"logical_block_bytes={logical_block}; pinned lbaf[0].ds=9 requires 512-byte LBAs; investigate format/source mismatch")
    slc_bytes = values["slc_total_lines"] * values["slc_pages_per_superblock"] * HOST_PAGE_BYTES
    qlc_bytes = values["qlc_total_lines"] * values["qlc_pages_per_superblock"] * HOST_PAGE_BYTES
    storage_bytes = reserved - MIB  # main.c excludes the first 1 MiB from storage.
    # conv_init_namespace first floors size*100/107; Identify Namespace then
    # floors that result to whole LBAs. OP reduces advertised logical capacity,
    # not the physical geometry initialized from the full storage size.
    logical_before_alignment = storage_bytes * 100 // SOURCE_PBA_PERCENT
    expected_namespace_bytes = logical_before_alignment // NVME_LBA_BYTES * NVME_LBA_BYTES
    if slc_bytes + qlc_bytes != storage_bytes:
        fail("modeled physical SLC/QLC geometry and reserved storage capacities disagree")
    if namespace_bytes != expected_namespace_bytes:
        fail(f"namespace bytes={namespace_bytes}; expected {expected_namespace_bytes} after source OP7% and 512-byte LBA alignment")
    return {
        **{key: values[key] for key in EXPECTED_GEOMETRY},
        "modeled_host_page_bytes": HOST_PAGE_BYTES,
        "modeled_slc_bytes": slc_bytes, "modeled_qlc_bytes": qlc_bytes,
        "modeled_total_bytes": slc_bytes + qlc_bytes,
        "slc_physical_blocks": values["slc_total_lines"] * values["die_count"],
        "qlc_physical_blocks": values["qlc_total_lines"] * values["die_count"],
        "physical_memory_start_bytes": start,
        "physical_memory_end_exclusive_bytes": start + reserved,
        "reserved_memmap_bytes": reserved, "metadata_prefix_bytes": MIB,
        "data_backing_bytes": storage_bytes, "namespace_advertised_bytes": namespace_bytes,
        "source_op_area_percent": 7,
        "source_physical_to_logical_percent": SOURCE_PBA_PERCENT,
        "source_logical_bytes_before_lba_alignment": logical_before_alignment,
        "expected_namespace_advertised_bytes": expected_namespace_bytes,
        "namespace_lba_bytes": NVME_LBA_BYTES,
        "namespace_lba_format_source": "admin.c lbaf[0].ds=9, flbas=0; queue logical-block readback must match",
        "namespace_lba_alignment_loss_bytes": logical_before_alignment - expected_namespace_bytes,
        "physical_minus_advertised_bytes": storage_bytes - expected_namespace_bytes,
        "modeled_physical_capacity_rounding_bytes": slc_bytes + qlc_bytes - storage_bytes,
        "namespace_sysfs_sector_unit_bytes": 512,
        "logical_block_bytes": logical_block, "physical_block_bytes": physical_block,
        "scope": "emulated geometry and physical DRAM addresses; not measured physical NAND capacity",
    }


def parse_die_stats(raw, die_count=4):
    # Getter omits dies whose host-read count is zero; empty output is correct.
    result = {str(i): {"reads": 0, "conflicts": 0, "wait_ns": 0} for i in range(die_count)}
    seen = set()
    for line in raw.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"die([0-9]+) reads=([0-9]+) conflicts=([0-9]+) wait_ns=([0-9]+)", line.strip())
        if not match:
            fail("unrecognized die_stats line: " + line)
        die = int(match[1])
        if die >= die_count or die in seen:
            fail("duplicate or out-of-geometry die_stats entry")
        seen.add(die)
        result[str(die)] = dict(zip(("reads", "conflicts", "wait_ns"), map(int, match.groups()[1:])))
    return result


def parse_bg_stats(raw):
    result = {}
    for token in raw.split():
        match = re.fullmatch(r"([a-z0-9_]+)=([0-9]+)", token)
        if not match or match[1] in result:
            fail("unrecognized or duplicate bg_nand_stats field: " + token)
        result[match[1]] = int(match[2])
    for key in BG_FIELDS:
        if key not in result:
            fail("required bg_nand_stats field absent: " + key)
    return result


def verify_reset(die_raw, bg_raw):
    dies = parse_die_stats(die_raw)
    background = parse_bg_stats(bg_raw)
    if any(value for die in dies.values() for value in die.values()):
        fail("host die read/conflict/wait counters are nonzero after baseline reset")
    if any(background[key] for key in BG_FIELDS[7:]):
        fail("host write or foreground-priority counters are nonzero after baseline reset")
    return {
        "requested_write": "1", "host_counter_zero_verified": True,
        "die_stats": dies, "bg_nand_stats": background,
        "background_activity_after_reset": any(background[key] for key in BG_FIELDS[:7]),
        "scope": "die_stats counts non-GC reads; background NAND may grow after reset and forms a baseline, not a reset failure",
        "reset_control_readback_note": "kernel setter does not store 1; readback need not equal requested write",
    }


class Evidence:
    """Persist successful reads immediately, including before later validation fails."""
    def __init__(self, out, record):
        self.out, self.record = out, record
        self.interfaces = {}

    def read(self, path, name):
        path = Path(path)
        try:
            raw = path.read_text()
            target = self.out / (name + ".txt")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(raw)
            self.interfaces[name] = {
                "path": str(path), "artifact": str(target.relative_to(self.out)),
                "time_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns(),
                "sha256": hashlib.sha256(raw.encode()).hexdigest(),
            }
            return raw
        except (OSError, UnicodeError) as exc:
            self.interfaces[name] = {"path": str(path), "error": str(exc)}
            fail(f"required interface {path}: {exc}")

    def command(self, name, argv, required=True):
        try:
            result = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            (self.out / (name + ".txt")).write_text(result.stdout)
            self.record.setdefault("commands", {})[name] = {"argv": argv, "returncode": result.returncode}
            if result.returncode and required:
                fail(f"{name} failed with exit {result.returncode}")
            return result.stdout
        except OSError as exc:
            self.record.setdefault("commands", {})[name] = {"argv": argv, "error": str(exc)}
            if required:
                fail(f"required command {name}: {exc}")
            return ""

    def persist(self):
        self.record["interfaces"] = self.interfaces
        (self.out / "snapshot.json").write_text(json.dumps(self.record, indent=2) + "\n")


def capture_runtime(evidence, phases, params, prefix=""):
    sources = {}
    # Read reset readbacks first; background NAND counters can advance meanwhile.
    for leaf in ("die_stats", "bg_nand_stats", "die_stats_reset"):
        sources[leaf] = evidence.read(params / leaf, prefix + leaf)
    for path in phases:
        for leaf in ("test_phase", "test_phase_stats", "superblock_stats"):
            key = path.parent.name + "_" + leaf
            sources[key] = evidence.read(path.parent / leaf, prefix + key)
    return sources


def main():
    if len(sys.argv) != 5:
        raise SystemExit("usage: phase_hook.py EVENT OUTPUT_DIR VARIANT NVME_DEVICE_NAME")
    event, directory, variant, device = sys.argv[1:]
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=False)
    record = {
        "schema_version": 2, "status": "collecting", "event": event,
        "time_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns(),
        "variant": variant, "device": "/dev/" + device,
        "scope": "source/runtime snapshot; zero active counters do not prove modeled NAND fully drained",
    }
    evidence = Evidence(out, record)
    error = None
    try:
        if variant not in MECHANISMS or not re.fullmatch(r"nvme[0-9]+n[0-9]+", device):
            fail("unsupported protocol variant or namespace name")
        phases = sorted(Path("/sys/kernel/debug/nvmev").glob("ftl*/test_phase"))
        record["ftl_paths"] = [str(path) for path in phases]
        params = Path("/sys/module/nvmev/parameters")
        block_root = Path("/sys/class/block") / device
        record["model"] = evidence.read(block_root / "device/model", "device_model").strip()
        record["serial"] = evidence.read(block_root / "device/serial", "device_serial").strip()
        controls = {key: evidence.read(params / key, "control_" + key).strip() for key in (
            "gc_nand_timing", "test_phase_recent_write_guard", "test_phase_guard_read_reqs",
            "memmap_start", "memmap_size", "cpus",
        )}
        record["controls"] = controls
        block = {key: evidence.read(block_root / leaf, "device_" + key).strip() for key, leaf in (
            ("sectors_512", "size"), ("logical_block_bytes", "queue/logical_block_size"),
            ("physical_block_bytes", "queue/physical_block_size"),
        )}
        record["block_readback"] = block
        profile = evidence.read("/proc/nvmev/latency_profile", "latency_profile").strip()
        record["latency_profile"] = profile
        before = capture_runtime(evidence, phases, params, "before_reset/" if event == "baseline" else "")
        if len(phases) != 1:
            fail("this protocol requires exactly one FTL; found " + str(len(phases)))
        if not record["model"].startswith("CSL_Virt_MN_") or not record["serial"].startswith("CSL_Virt_SN_"):
            fail("target is not the expected emulated controller")
        mounts = evidence.command("device_mounts", ["lsblk", "-nr", "-o", "MOUNTPOINT", "/dev/" + device])
        if mounts.strip():
            fail("target or child partition is mounted")
        swaps = evidence.read("/proc/swaps", "swaps")
        if any(line.split()[0].startswith("/dev/" + device) for line in swaps.splitlines()[1:] if line.split()):
            fail("target used as swap")
        if profile != "normal" or controls["gc_nand_timing"] != "1":
            fail("normal timing profile and gc_nand_timing=1 are required")
        if controls["test_phase_recent_write_guard"].lower() not in ("1", "y", "yes", "true"):
            fail("recent_write_guard must be enabled")
        if controls["test_phase_guard_read_reqs"] != "256":
            fail("guard_read_reqs must read 256")
        ftl = phases[0].parent.name
        record["mechanisms"] = {
            "test_phase_stats": verify_mechanism(before[ftl + "_test_phase_stats"], variant, "test_phase_stats"),
            "superblock_stats": verify_mechanism(before[ftl + "_superblock_stats"], variant, "superblock_stats"),
        }
        record["geometry"] = verify_geometry(before[ftl + "_superblock_stats"], controls, block)
        for name, argv in (
            ("lsblk", ["lsblk", "-b", "-J", "-o", "NAME,SIZE,MODEL,SERIAL,MOUNTPOINT"]),
            ("kernel", ["uname", "-a"]),
        ):
            evidence.command(name, argv)
        # An insmod-only module need not be installed in /lib/modules, so this
        # name lookup is diagnostic. The loaded srcversion and selected .ko
        # lookup below are the required identity checks.
        evidence.command("module", ["modinfo", "nvmev"], required=False)
        record["loaded_srcversion"] = evidence.read("/sys/module/nvmev/srcversion", "loaded_srcversion").strip()
        if event == "preinit":
            module = Path(__file__).resolve().parent / "nvmev_on.ko"
            expected = evidence.command("selected_module_srcversion", ["modinfo", "-F", "srcversion", str(module)]).strip()
            record["selected_module_sha256"] = hashlib.sha256(module.read_bytes()).hexdigest()
            if not expected or expected != record["loaded_srcversion"]:
                fail("loaded module srcversion mismatch")
        sources = before
        if event == "baseline":
            record["before_reset_stats"] = numeric_stats(before[ftl + "_test_phase_stats"])
            record["reset_actions"] = []
            for path in phases:
                path.write_text("1\n")
                record["reset_actions"].append({"path": str(path), "write": "1", "monotonic_ns": time.monotonic_ns()})
                if evidence.read(path, "test_phase_after_enter").strip() != "1":
                    fail("test_phase readback failed")
            # This callback clears counters but does not store its input value.
            (params / "die_stats_reset").write_text("1\n")
            record["reset_actions"].append({"path": str(params / "die_stats_reset"), "write": "1", "monotonic_ns": time.monotonic_ns()})
            sources = capture_runtime(evidence, phases, params)
            record["reset_validation"] = verify_reset(sources["die_stats"], sources["bg_nand_stats"])
        stats = numeric_stats(sources[ftl + "_test_phase_stats"])
        record["stats"] = stats
        record["die_stats"] = parse_die_stats(sources["die_stats"])
        record["bg_nand_stats"] = parse_bg_stats(sources["bg_nand_stats"])
        for key in ("active", "active_reads", "active_overwrites", "active_bg_ops", "slc_to_qlc_migration_pages",
                    "slc_gc_valid_copy_pages", "slc_gc_invalid_reclaimed_pages", "internal_write_pages_est",
                    "bg_repromote_ops", "bg_qlc_rebalance_ops"):
            if key not in stats:
                fail("required counter absent: " + key)
        if event == "baseline" and stats["active"] != 1:
            fail("measurement phase not active")
        record["status"] = "ok"
    except BaseException as exc:
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"
        error = exc
    finally:
        record["time_finished_ns"] = time.time_ns()
        evidence.persist()
    if error is not None:
        raise SystemExit("PROTOCOL FAIL: " + str(error))
    print("protocol snapshot:", event, out)


if __name__ == "__main__":
    main()
