#!/usr/bin/env bash
# Convenience entry. preview/probe do not load a module or access a device.
set -euo pipefail
[[ $# -ge 2 ]] || { echo "Usage: bash experiments/run.sh prepare|build|preview|pilot|formal|diag|probe HOST_CONFIG [NEW_OUTPUT]" >&2; exit 2; }
fw_action="$1"
fw_config="$2"
fw_native="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$fw_config"
case "$fw_action" in
  prepare)
    : "${FW_REPO:?}" "${FW_WORKSPACE:?}" "${FW_DEVICE_NAME:?}"
    python3 "$fw_native/protocol.py" prepare --repo "$FW_REPO" --workspace "$FW_WORKSPACE" --device-name "$FW_DEVICE_NAME"
    ;;
  build)
    bash "$fw_native/build_all.sh" "$FW_WORKSPACE" "${3:-$FW_RESULTS/build_v1}"
    ;;
  preview|pilot|formal|diag)
    bash "$fw_native/run_matrix.sh" "$fw_action" "$fw_config" "${3:-$FW_RESULTS/${fw_action}_v1}"
    ;;
  probe)
    python3 "$fw_native/mechanism_probe.py" --repo "$FW_REPO" --out "${3:-$FW_RESULTS/source_probe_v1}"
    ;;
  *) echo "Unknown action: $fw_action" >&2; exit 2 ;;
esac
