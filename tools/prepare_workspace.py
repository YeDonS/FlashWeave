#!/usr/bin/env python3
"""Assemble the split module/workload snapshot without changing source files."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATTERNS = ("conv_ftl*.c", "conv_ftl*.h", "ssd*.c", "ssd*.h", "channel_model.*", "main.c", "io.c", "admin.c", "nvmev.h", "build_die.sh")
WORKLOAD_PATTERNS = ("sqlite_*.c", "sqlite_*.sh", "fio_*", "fileserver_*", "run_sqlite_then_fio_die_variants.sh", "analyze_latency_run.py", "plot_table_die_transition.py", "test_analyze_latency_run.py")


def prepare(root: Path, output: Path) -> dict:
    output = output.resolve()
    if output.exists():
        raise ValueError(f"Output already exists; choose a new directory: {output}")
    if output == root.resolve() or output in root.resolve().parents:
        raise ValueError("Output must not replace the repository or an ancestor")
    for source_root in (root / "nvmevirt_DA", root / "evaluation"):
        if source_root.resolve() in output.parents:
            raise ValueError("Output must not be inside a copied source directory")
    for path in (root / "nvmevirt_DA/Makefile", root / "evaluation/commonvariables.sh", root / "build_die.sh"):
        if not path.is_file():
            raise ValueError(f"Missing required source: {path}")
    module = output / "nvmevirt_DA"
    evaluation = output / "evaluation"
    ignore = shutil.ignore_patterns("*.ko", "*.o", "*.cmd", "*.bak", "*.mod*", "Module.symvers", "modules.order", "__pycache__", "result", "device")
    shutil.copytree(root / "nvmevirt_DA", module, ignore=ignore)
    shutil.copytree(root / "evaluation", evaluation, ignore=ignore)
    for patterns, destination in ((MODULE_PATTERNS, module), (WORKLOAD_PATTERNS, evaluation)):
        for pattern in patterns:
            for source in root.glob(pattern):
                if source.is_file() and source.suffix in (".c", ".h", ".sh", ".py", ".fio", ".f"):
                    if destination == evaluation and source.name.startswith("sqlite_") and source.suffix == ".c":
                        target = destination / "sqlite" / source.name
                    elif destination == evaluation and source.suffix == ".f":
                        target = destination / "filebench" / source.name
                    else:
                        target = destination / source.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    # The inherited Makefile ignores build_die.sh's integrated-ablation flags.
    # Wire them into Kbuild in the disposable workspace only.
    makefile = module / "Makefile"
    text = makefile.read_text()
    if "ccflags-y += $(NVMEV_EXTRA_CFLAGS)" not in text:
        text = "# Forward build_die.sh ablation flags to Kbuild.\nccflags-y += $(NVMEV_EXTRA_CFLAGS)\n\n" + text
    makefile.write_text(text)
    inputs = {}
    for path in sorted(output.rglob("*")):
        if path.is_file():
            inputs[path.relative_to(output).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    record = {"schema_version": 1, "purpose": "Staged build inputs; not evidence of a successful build or workload run", "files_sha256": inputs}
    (output / "workspace_inputs.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".workspaces/nvmevirt")
    args = parser.parse_args()
    try:
        record = prepare(ROOT, args.output)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    print(f"Prepared {len(record['files_sha256'])} files under {args.output.resolve()}")
    print("Module: nvmevirt_DA/; workloads: evaluation/. No kernel build or device operation was run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
