#!/usr/bin/env python3
"""Compile isolated scheduling functions from the current frozen repository.

This is a NEW, read-only source-level probe. It neither loads a kernel module
nor opens a block device. An observed negative-control failure is a result,
not a process failure. The probe does not validate the complete FTL, host
completion propagation, physical NAND issue, or hardware preemption behavior.

Example:
  python3 mechanism_probe.py --repo /path/to/pinned/FlashWeave --out /fresh/probe

Exit 0: compilation/execution and observation capture succeeded, including when
        the nonpreemption negative control is not satisfied.
Exit 2: preparation, source identity, compiler, or execution failed. Evidence is
        preserved in --out when that fresh directory could safely be created.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


from provenance import freeze_source
FUNCTIONS = (
    "max3_u64",
    "ssd_lun_sched_start",
    "ssd_lun_commit_normal",
    "ssd_lun_commit_low_priority",
    "ssd_lun_commit_read_priority",
)
MACROS = ("QLC_PROG_LATENCY", "QLC_4KB_READ_LATENCY_TOP")
LIMITATIONS = [
    "Only exact extracted LUN availability functions execute in a userspace C harness.",
    "spinlock_t is an unused userspace placeholder; this is not a kernel ABI or locking test.",
    "The harness supplies synthetic model times and uses two unmodified source timing macros.",
    "There is no actual NAND issue/start/completion event or already-issued state in this probe.",
    "A read starting before an LP reservation end does not establish physical NAND preemption.",
    "T3 covers separate nand_lun objects only, not shared channel/PCIe contention or host mapping.",
    "T1 queued-before-issue arbitration requires a real pending/issue barrier and event trace.",
    "T4 bounded worker yield/requeue/forced progress requires real worker/maintenance events.",
    "T5 emergency progress requires real pressure, victim, reclamation and completion events.",
    "Whole-FTL execution, maintenance debt/drain and host CQ target propagation remain unverified.",
]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def command(args: list[str], *, cwd: Path | None = None, timeout: int = 30):
    try:
        return subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        # Keep partial compiler/probe output even on a timeout.
        def decoded(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
        return subprocess.CompletedProcess(
            args, 124, decoded(exc.stdout),
            decoded(exc.stderr) + f"\nCommand exceeded the {timeout}s timeout.\n"
        )


def git_output(repo: Path, args: list[str]) -> bytes:
    process = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, timeout=30, check=False
    )
    if process.returncode:
        raise RuntimeError(
            "git command failed: " + process.stderr.decode("utf-8", errors="replace").strip()
        )
    return process.stdout


def closing_brace(text: str, opening: int) -> int:
    """Find a C block's end while ignoring comments and quoted braces."""
    depth, index, state = 0, opening, "code"
    while index < len(text):
        ch = text[index]
        following = text[index : index + 2]
        if state == "line_comment":
            if ch == "\n":
                state = "code"
        elif state == "block_comment":
            if following == "*/":
                state = "code"
                index += 1
        elif state in ("string", "char"):
            if ch == "\\":
                index += 1
            elif (state == "string" and ch == '"') or (state == "char" and ch == "'"):
                state = "code"
        elif following == "//":
            state = "line_comment"
            index += 1
        elif following == "/*":
            state = "block_comment"
            index += 1
        elif ch == '"':
            state = "string"
        elif ch == "'":
            state = "char"
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise ValueError("Unterminated source block")


def source_block(text: str, name: str, kind: str) -> dict:
    import re

    if kind == "function":
        pattern = rf"(?m)^static[ \t]+inline[ \t]+(?:uint64_t|void)[ \t]+{re.escape(name)}[ \t]*\("
    else:
        pattern = rf"(?m)^struct[ \t]+{re.escape(name)}[ \t]*\{{"
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {kind} definition: {name}, got {len(matches)}")
    start = matches[0].start()
    opening = text.find("{", matches[0].start())
    if opening < 0:
        raise ValueError(f"No body for {name}")
    end = closing_brace(text, opening) + 1
    if kind == "struct":
        if text[end : end + 1] != ";":
            raise ValueError(f"Unexpected trailing syntax for struct {name}")
        end += 1
    content = text[start:end]
    return {
        "name": name,
        "kind": kind,
        "line_start": text.count("\n", 0, start) + 1,
        "line_end": text.count("\n", 0, end) + 1,
        "sha256": sha256(content.encode("utf-8")),
        "text": content,
    }


def source_macro(text: str, name: str) -> dict:
    import re

    matches = list(re.finditer(rf"(?m)^#define[ \t]+{re.escape(name)}[ \t]+[^\n]+", text))
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one macro: {name}")
    match = matches[0]
    if match.group().rstrip().endswith("\\"):
        raise ValueError(f"Multiline macro not supported by this pinned probe: {name}")
    content = match.group()
    return {
        "name": name,
        "kind": "macro",
        "line_start": text.count("\n", 0, match.start()) + 1,
        "line_end": text.count("\n", 0, match.end()) + 1,
        "sha256": sha256(content.encode("utf-8")),
        "text": content,
    }


PRELUDE = r'''/* Generated source-level probe. Extracted blocks below are unchanged.
 * Userspace compatibility ONLY: kernel spinlock storage is an unused opaque
 * placeholder. No lock member is read or written. max() handles uint64_t inputs
 * and U64_MAX aliases UINT64_MAX. No kernel ABI/layout equivalence is claimed.
 */
#include <stdbool.h>
#include <stdint.h>
#include <inttypes.h>
#include <stdio.h>
typedef struct { unsigned char unused; } spinlock_t;
static inline uint64_t max(uint64_t a, uint64_t b) { return a > b ? a : b; }
#define U64_MAX UINT64_MAX
'''


HARNESS = r'''
/* Synthetic inputs below invoke the extracted functions, not a rewritten model. */
int main(void)
{
    const uint64_t arrival = 100000; /* 100 us, supplied synthetic arrival. */
    const uint64_t read_service = QLC_4KB_READ_LATENCY_TOP;
    const uint64_t program_service = QLC_PROG_LATENCY;
    struct nand_lun idle = {0};
    struct nand_lun occupied = {0};
    struct nand_lun other_die = {0};
    uint64_t r0_start = ssd_lun_sched_start(&idle, arrival, true, false);
    uint64_t r0_end = r0_start + read_service;
    ssd_lun_commit_read_priority(&idle, r0_start, r0_end);

    /* LP is a reservation [0, program_service]. No real issue event occurs. */
    uint64_t m_start = ssd_lun_sched_start(&occupied, 0, false, true);
    uint64_t m_original_end = m_start + program_service;
    ssd_lun_commit_low_priority(&occupied, m_original_end);
    uint64_t normal_r_start = ssd_lun_sched_start(&occupied, arrival, false, false);
    uint64_t hp_r_start = ssd_lun_sched_start(&occupied, arrival, true, false);
    uint64_t hp_r_end = hp_r_start + read_service;
    ssd_lun_commit_read_priority(&occupied, hp_r_start, hp_r_end);

    struct nand_lun independent_a = {0};
    uint64_t independent_m_start = ssd_lun_sched_start(&independent_a, 0, false, true);
    ssd_lun_commit_low_priority(&independent_a, independent_m_start + program_service);
    uint64_t a_before = independent_a.lp_next_lun_avail_time;
    uint64_t b_r_start = ssd_lun_sched_start(&other_die, arrival, true, false);
    ssd_lun_commit_read_priority(&other_die, b_r_start, b_r_start + read_service);

    printf("{\"time_unit\":\"ns\",\"synthetic_arrival_ns\":%" PRIu64
           ",\"source_macro_values\":{\"QLC_PROG_LATENCY\":%" PRIu64
           ",\"QLC_4KB_READ_LATENCY_TOP\":%" PRIu64 "},\"cases\":[",
           arrival, program_service, read_service);
    printf("{\"id\":\"T0_idle\",\"read_start_ns\":%" PRIu64
           ",\"read_end_ns\":%" PRIu64 ",\"next_available_ns\":%" PRIu64
           ",\"starts_at_arrival\":%s},", r0_start, r0_end, idle.next_lun_avail_time,
           r0_start == arrival ? "true" : "false");
    printf("{\"id\":\"T2_existing_lp_reservation\",\"maintenance_start_ns\":%" PRIu64
           ",\"maintenance_original_end_ns\":%" PRIu64
           ",\"normal_read_start_ns\":%" PRIu64 ",\"priority_read_start_ns\":%" PRIu64
           ",\"priority_read_end_ns\":%" PRIu64 ",\"lp_available_after_read_ns\":%" PRIu64
           ",\"hp_available_after_read_ns\":%" PRIu64
           ",\"priority_read_starts_before_original_lp_end\":%s"
           ",\"nonpreemption_negative_control_satisfied\":%s},",
           m_start, m_original_end, normal_r_start, hp_r_start, hp_r_end,
           occupied.lp_next_lun_avail_time, occupied.hp_next_lun_avail_time,
           hp_r_start < m_original_end ? "true" : "false",
           hp_r_start >= m_original_end ? "true" : "false");
    printf("{\"id\":\"T3_separate_lun_objects\",\"other_lun_read_start_ns\":%" PRIu64
           ",\"occupied_lun_lp_before_ns\":%" PRIu64
           ",\"occupied_lun_lp_after_ns\":%" PRIu64 ",\"separate_state_unchanged\":%s"
           ",\"other_lun_starts_at_arrival\":%s}]}\n", b_r_start, a_before,
           independent_a.lp_next_lun_avail_time,
           independent_a.lp_next_lun_avail_time == a_before ? "true" : "false",
           b_r_start == arrival ? "true" : "false");
    return 0; /* Negative-control observations intentionally do not cause a crash. */
}
'''


def make_probe(extracts: list[dict]) -> str:
    pieces = [PRELUDE]
    for item in extracts:
        # The block text is copied byte-for-byte after UTF-8 decoding; #line is
        # only a compiler provenance directive outside the extracted block.
        pieces.append(f'\n#line {item["line_start"]} "{item["file"]}"\n')
        pieces.append(item["text"] + "\n")
    pieces.append('\n#line 1 "synthetic_probe_harness"\n')
    pieces.append(HARNESS)
    return "".join(pieces)


def report_text(result: dict) -> str:
    lines = [
        "FlashWeave isolated source-level mechanism probe",
        "Scope: unmodified extracted timing functions, not kernel/device execution.",
        "Process status: " + result["status"],
        "Frozen source commit: " + str(result.get("source_commit")),
        "Base HEAD: " + str(result.get("base_commit")),
        "",
    ]
    if "error" in result:
        lines += ["Infrastructure/preparation error: " + result["error"], ""]
    observation = result.get("observations", {})
    for case in observation.get("cases", []):
        lines += [case["id"], json.dumps(case, indent=2, sort_keys=True), ""]
    if result.get("interpretation"):
        lines += ["Interpretation", result["interpretation"], ""]
    lines += ["Unverified work / limitations", *["- " + value for value in LIMITATIONS], ""]
    return "\n".join(lines)


def run_probe(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve()
    requested_out = Path(args.out).expanduser()
    if requested_out.is_symlink():
        print("ERROR: --out may not be an existing symlink", file=sys.stderr)
        return 2
    out = requested_out.resolve()
    # Always refuse a destination inside the repository, including ignored paths.
    if out == repo or repo in out.parents:
        print("ERROR: --out must be outside the read-only repository", file=sys.stderr)
        return 2
    if out.exists() or out.is_symlink():
        print("ERROR: --out must be a fresh, nonexistent directory", file=sys.stderr)
        return 2
    try:
        out.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        print(f"ERROR: could not create fresh output directory: {exc}", file=sys.stderr)
        return 2

    result = {
        "schema": "flashweave-isolated-source-probe-v1",
        "status": "preparing",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo": str(repo),
        "out": str(out),
        "scope": "isolated_source_level_only",
        "not_a_complete_mechanism_validation": True,
        "limitations": LIMITATIONS,
        "commands": [],
    }
    exit_status = 2
    try:
        identity = freeze_source(repo, args.allow_dirty_preview)
        result.update(identity)
        head = identity["base_commit"]

        sources, texts = {}, {}
        for name in ("ssd.h", "ssd_die.c", "ssd_config.h"):
            local = (repo / name).read_bytes()
            pinned = git_output(repo, ["show", f"{head}:{name}"])
            if local != pinned and not args.allow_dirty_preview:
                raise ValueError(f"Source changed after freezing: {name}")
            sources[name] = {
                "path": str(repo / name),
                "sha256": sha256(local),
                "base_git_blob": git_output(repo, ["rev-parse", f"{head}:{name}"]).decode().strip(),
                "matches_base_commit": local == pinned,
            }
            texts[name] = local.decode("utf-8")

        extracts = []
        struct = source_block(texts["ssd.h"], "nand_lun", "struct")
        struct["file"] = "ssd.h"
        extracts.append(struct)
        for name in MACROS:
            block = source_macro(texts["ssd_config.h"], name)
            block["file"] = "ssd_config.h"
            extracts.append(block)
        for name in FUNCTIONS:
            block = source_block(texts["ssd_die.c"], name, "function")
            block["file"] = "ssd_die.c"
            extracts.append(block)
        provenance = {
            "repo": str(repo), "source_identity": identity, "sources": sources,
            "extracted_blocks": extracts,
            "compatibility_adapters": [
                "spinlock_t: unused opaque userspace placeholder, not ABI-compatible",
                "max: uint64_t-only inline compatibility helper; inputs here have no side effects",
                "U64_MAX: alias to standard UINT64_MAX",
            ],
            "source_logic_rewritten": False,
        }
        save_json(out / "provenance.json", provenance)
        result["source_sha256"] = {name: info["sha256"] for name, info in sources.items()}
        (out / "probe.c").write_text(make_probe(extracts), encoding="utf-8")

        compiler = args.cc or shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
        if not compiler:
            raise RuntimeError("No C compiler found; source excerpts were preserved")
        compiler_path = shutil.which(compiler)
        if not compiler_path:
            raise RuntimeError(f"C compiler is unavailable: {compiler}")
        version = command([compiler_path, "--version"], timeout=15)
        (out / "compiler_version.txt").write_text(version.stdout + version.stderr, encoding="utf-8")
        result["compiler"] = {"path": compiler_path, "version_exit_code": version.returncode}
        if version.returncode:
            raise RuntimeError("Compiler version command failed")

        compile_args = [compiler_path, "-std=c11", "-O0", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-function", str(out / "probe.c"), "-o", str(out / "probe")]
        result["commands"].append(compile_args)
        compiled = command(compile_args)
        (out / "compile_stdout.txt").write_text(compiled.stdout, encoding="utf-8")
        (out / "compile_stderr.txt").write_text(compiled.stderr, encoding="utf-8")
        result["compile_exit_code"] = compiled.returncode
        if compiled.returncode:
            raise RuntimeError("Probe compilation failed; compiler output and source were preserved")

        result["commands"].append([str(out / "probe")])
        executed = command([str(out / "probe")], timeout=10)
        (out / "probe_stdout.json").write_text(executed.stdout, encoding="utf-8")
        (out / "probe_stderr.txt").write_text(executed.stderr, encoding="utf-8")
        result["probe_exit_code"] = executed.returncode
        if executed.returncode:
            raise RuntimeError("Probe process failed; raw stdout/stderr were preserved")
        observations = json.loads(executed.stdout)
        cases = {case["id"]: case for case in observations["cases"]}
        result["observations"] = observations
        result["source_level_checks"] = {
            "T0_idle_starts_at_arrival": cases["T0_idle"]["starts_at_arrival"],
            "T2_nonpreemption_negative_control_satisfied": cases["T2_existing_lp_reservation"][
                "nonpreemption_negative_control_satisfied"],
            "T3_separate_lun_state_unchanged": cases["T3_separate_lun_objects"][
                "separate_state_unchanged"],
            "T3_other_lun_starts_at_arrival": cases["T3_separate_lun_objects"][
                "other_lun_starts_at_arrival"],
        }
        crossed = cases["T2_existing_lp_reservation"]["priority_read_starts_before_original_lp_end"]
        if crossed:
            result["interpretation"] = (
                "The extracted high-priority timing functions allow the synthetic read to start "
                "before the existing low-priority reservation's original end and extend the LP "
                "availability tail. The isolated T2 nonpreemption negative control is therefore "
                "not satisfied. This does not prove actual NAND preemption: no issued/in-flight "
                "operation or real event trace executes here. Nonpreemption cannot be claimed "
                "from these availability functions alone. Whole-FTL issue/completion propagation "
                "and T1/T4/T5 require separate instrumentation and integration tests."
            )
        else:
            result["interpretation"] = (
                "This synthetic T2 input did not bypass the existing LP reservation. This is "
                "only an isolated timing-function observation and does not establish complete "
                "FTL or physical NAND nonpreemption. T1/T4/T5 and real issue/completion traces "
                "remain required."
            )
        # Verify that this tool did not silently depend on modified source input.
        for name, info in sources.items():
            if sha256((repo / name).read_bytes()) != info["sha256"]:
                raise RuntimeError(f"Source changed concurrently during the probe: {name}")
        result["status"] = "source_level_observations_recorded"
        exit_status = 0
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.TimeoutExpired) as exc:
        result["status"] = "infrastructure_or_preparation_error"
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        result["exit_code_meaning"] = "Process success does not mean the negative control passed."
        result["script_sha256"] = sha256(Path(__file__).read_bytes())
        (out / "report.txt").write_text(report_text(result), encoding="utf-8")
        result["artifact_sha256"] = {
            path.name: sha256(path.read_bytes()) for path in sorted(out.iterdir())
            if path.is_file() and path.name != "result.json"
        }
        result["artifact_hash_note"] = "result.json is excluded from its own hash inventory."
        save_json(out / "result.json", result)
    print(json.dumps({"status": result["status"], "out": str(out),
                      "checks": result.get("source_level_checks", {}),
                      "error": result.get("error")}, sort_keys=True))
    return exit_status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, help="Read-only current repository; clean committed revision required")
    parser.add_argument("--out", required=True, help="Fresh nonexistent directory outside the repository")
    parser.add_argument("--cc", help="Optional C compiler executable (no shell or extra flags)")
    parser.add_argument("--allow-dirty-preview", action="store_true", help="development source snapshot only; never certifies committed/device execution")
    return run_probe(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
