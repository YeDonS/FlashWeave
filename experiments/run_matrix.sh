#!/usr/bin/env bash
# NEW helper. "preview" only generates jobs; "pilot" and "formal" perform raw I/O.
set -euo pipefail
[[ $# == 3 ]] || { echo "Usage: bash run_matrix.sh preview|pilot|formal|diag HOST_CONFIG NEW_BATCH_DIR" >&2; exit 2; }
fw_mode="$1"
case "$fw_mode" in preview|pilot|formal|diag) ;; *) echo "Unknown mode" >&2; exit 2 ;; esac
source "$2"
fw_batch="$3"
fw_pkg="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${FW_WORKSPACE:?}"
[[ ! -e "$fw_batch" ]] || { echo "Batch already exists; preserve it and choose a new directory" >&2; exit 1; }
mkdir -p "$fw_batch"
fw_batch="$(cd "$fw_batch" && pwd)"
cp "$2" "$fw_batch/host_config_used.sh"
cp "$fw_pkg/run_matrix.csv" "$fw_batch/matrix_reference.csv"
if [[ "$fw_mode" != preview ]]; then
  [[ "$(uname -s)" == Linux && "$EUID" == 0 ]] || { echo "Linux root required" >&2; exit 1; }
  : "${FW_MEMMAP_START:?}" "${FW_CPUS:?}" "${FW_HOST_CPUS:?}"
fi
run_case() {
  local fw_cell="$1" fw_seed="$2" fw_position="$3"
  local fw_out="$fw_batch/seed_${fw_seed}_pos_${fw_position}_${fw_cell}"
  if [[ "$fw_mode" == preview ]]; then
    python3 "$fw_pkg/protocol.py" preview --workspace "$FW_WORKSPACE" --cell "$fw_cell" --seed "$fw_seed" --out "$fw_out"
  else
    local fw_extra=(--trace-mode off)
    if [[ "$fw_mode" == diag ]]; then fw_extra=(--profile pressure --trace-mode diag --trace-max-bytes "${FW_TRACE_MAX_BYTES:-8589934592}"); fi
    python3 "$fw_pkg/protocol.py" run --workspace "$FW_WORKSPACE" --cell "$fw_cell" --seed "$fw_seed" --out "$fw_out" \
      --memmap-start "$FW_MEMMAP_START" --cpus "$FW_CPUS" --host-cpus "$FW_HOST_CPUS" "${fw_extra[@]}"
    python3 "$fw_pkg/protocol.py" check --run "$fw_out" > "$fw_out/validation.txt" 2>&1
    # validation.txt is analysis added after the immutable raw-artifact manifest.
  fi
}
if [[ "$fw_mode" == pilot || "$fw_mode" == diag ]]; then
  fw_position=0
  for fw_cell in A B D C; do
    fw_position=$((fw_position+1))
    if [[ "$fw_mode" == diag ]]; then
      run_case "$fw_cell" 9002 "$fw_position"
    else
      run_case "$fw_cell" 9001 "$fw_position"
    fi
  done
else
  {
    read -r fw_header
    while IFS=, read -r fw_block fw_seed fw_position fw_cell fw_variant fw_dataset; do
      run_case "$fw_cell" "$fw_seed" "$fw_position"
    done
  } < "$fw_pkg/run_matrix.csv"
fi
if [[ "$fw_mode" == formal ]]; then
  python3 "$fw_pkg/summarize.py" --batch "$fw_batch" --out "$fw_batch/analysis"
fi
echo "Batch finished: $fw_batch"

