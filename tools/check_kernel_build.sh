#!/usr/bin/env bash
# CI compile check only. Never loads nvmev or accesses a block device.
set -euo pipefail
: "${KERNELDIR:?set an installed Linux kernel headers directory}"
[[ "$(uname -s)" == Linux ]] || { echo "Linux headers build required" >&2; exit 1; }
fw_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fw_tmp="$(mktemp -d)"
trap 'rm -rf "$fw_tmp"' EXIT
python3 "$fw_root/experiments/protocol.py" prepare --repo "$fw_root" \
  --workspace "$fw_tmp/workspace" --device-name nvme4n1
fw_flags='-DNVMEV_LATENCY3_REQUEUE_DELAY_US=0U -DNVMEV_LATENCY3_FORCE_AFTER_YIELDS=8U -DNVMEV_LATENCY3_FORCE_CATCHUP_MAX=8U -DNVMEV_LATENCY3_READ_WINDOW_GATE_TOKENS=8U -DNVMEV_TEST_PHASE_READ_REQ_LATENCY_STATS=1 -DNVMEV_TEST_PHASE_READ_PRIORITY_DIAG=1'
cd "$fw_tmp/workspace/nvmevirt_DA"
NVMEV_EXTRA_VARIANT_CFLAGS="$fw_flags" bash build_die.sh \
  die_latency1_norp_sb die_latency1_qlc_all_norp_sb \
  die_latency3_norp_sb die_latency3_qlc_all_norp_sb
for fw_variant in die_latency1_norp_sb die_latency1_qlc_all_norp_sb die_latency3_norp_sb die_latency3_qlc_all_norp_sb; do
  test -s "nvmev_$fw_variant.ko"
done

