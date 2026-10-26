# Copy to experiments/host_config.sh on the Linux experiment host.
# Absolute paths must contain no spaces or shell metacharacters.
FW_PACKAGE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FW_REPO="$(cd "$FW_PACKAGE/.." && pwd)"
FW_WORKSPACE="$FW_REPO/.workspaces/ablation-v2"
FW_RESULTS="${FW_REPO}.results"

# Whole EMULATED namespace, for example nvme4n1. Identify it on this host.
FW_DEVICE_NAME=
# Actual kernel-reserved physical interval start; never copy a historical example.
# Required interval length is 65537 MiB, including 1 MiB emulator metadata.
# Manually verify the entire interval is reserved in /proc/iomem before running.
# This gives 64 GiB physical flash; OP7% makes the namespace about59.81GiB.
FW_MEMMAP_START=
# Comma-separated online CPUs: first dispatcher, remaining I/O workers.
FW_CPUS=
# Separate online host workload CPUs for taskset, ideally avoiding emulator CPUs.
FW_HOST_CPUS=

# Diagnostic batches only. Formal batches always collect no event trace.
FW_TRACE_MAX_BYTES=8589934592

