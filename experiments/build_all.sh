#!/usr/bin/env bash
# NEW helper: Linux build only; does not load a module or access a device.
set -euo pipefail
[[ $# == 2 ]] || { echo "Usage: bash build_all.sh WORKSPACE NEW_BUILD_ARTIFACT_DIR" >&2; exit 2; }
fw_ws="$(cd "$1" && pwd)"
fw_archive="$2"
[[ "$(uname -s)" == Linux ]] || { echo "Linux with matching kernel headers required" >&2; exit 1; }
[[ ! -e "$fw_archive" ]] || { echo "Build artifact directory exists" >&2; exit 1; }
mkdir -p "$fw_archive"
fw_archive="$(cd "$fw_archive" && pwd)"
cp "$fw_ws/protocol_inputs.json" "$fw_archive/"
cp "$fw_ws/protocol_build_flags.txt" "$fw_archive/"
uname -a > "$fw_archive/kernel.txt"
gcc --version > "$fw_archive/compiler.txt"
readlink -f "${KERNELDIR:-/lib/modules/$(uname -r)/build}" > "$fw_archive/kernel_headers.txt"
[[ -f "/boot/config-$(uname -r)" ]] && cp "/boot/config-$(uname -r)" "$fw_archive/kernel_config.txt"
fw_flags="$(cat "$fw_ws/protocol_build_flags.txt")"
(
  cd "$fw_ws/nvmevirt_DA"
  NVMEV_EXTRA_VARIANT_CFLAGS="$fw_flags" bash build_die.sh \
    die_latency1_norp_sb die_latency1_qlc_all_norp_sb \
    die_latency3_norp_sb die_latency3_qlc_all_norp_sb
) > "$fw_archive/build.log" 2>&1
for fw_variant in die_latency1_norp_sb die_latency1_qlc_all_norp_sb die_latency3_norp_sb die_latency3_qlc_all_norp_sb; do
  cp "$fw_ws/nvmevirt_DA/nvmev_$fw_variant.ko" "$fw_archive/"
  modinfo "$fw_ws/nvmevirt_DA/nvmev_$fw_variant.ko" > "$fw_archive/$fw_variant.modinfo.txt"
done
python3 - "$fw_ws" "$fw_archive" <<'PY'
import hashlib,json,sys
from pathlib import Path
ws,out=map(Path,sys.argv[1:])
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
inputs=json.loads((ws/"protocol_inputs.json").read_text())
for relative,want in inputs["files_sha256"].items():
    p=ws/relative
    if not p.is_file() or h(p)!=want:
        raise SystemExit("ERROR: source changed during build: "+relative)
result={"scope":"build artifacts only; no load/runtime certification",
        "source_commit":inputs["source_commit"],"base_commit":inputs["base_commit"],
        "source_dirty":inputs["source_dirty"],"protocol_version":inputs["protocol_version"],
        "build_flags":inputs["build_flags"],
        "protocol_inputs_sha256":h(ws/"protocol_inputs.json"),
        "modules":{variant:{"filename":"nvmev_"+variant+".ko",
                    "sha256":h(ws/"nvmevirt_DA"/("nvmev_"+variant+".ko"))}
                   for variant in ["die_latency1_norp_sb","die_latency1_qlc_all_norp_sb",
                                   "die_latency3_norp_sb","die_latency3_qlc_all_norp_sb"]},
        "files_sha256":{p.name:h(p) for p in sorted(out.iterdir()) if p.is_file()}}
headers=Path((out/"kernel_headers.txt").read_text().strip())
release=headers/"include/config/kernel.release"
result["kernel_release"]=release.read_text().strip() if release.is_file() else None
serialized=json.dumps(result,indent=2)+"\n"
(out/"build_manifest.json").write_text(serialized)
(ws/"protocol_build.json").write_text(serialized)
print("Built all four modules. Inspect build.log and modinfo before the pilot.")
PY

