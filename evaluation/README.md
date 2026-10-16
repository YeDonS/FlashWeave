# Workload entry points

Use the [Linux runbook](../docs/RUNBOOK.md) for setup and the [result archive](../results/README.md) for measurements. Inherited scripts/manual guides also reference older machines.

| Workload | Root sources / runner | Purpose |
| --- | --- | --- |
| fio | [runner](../fio_fragment_die_latency_mixed.sh), [config](../fio_latency_v2_mixed.fio) | Chunked initialization/prewarm and separate-region measured reads/writes |
| SQLite single file | [runner](../sqlite_fragment_die_test1_singlefile_pageflow_fileparallel_fullscan.sh), [C source](../sqlite_append_die_affinity_singlefile_pageflow_fileparallel.c) | Many logical tables in one database, pageflow initialization |
| SQLite table files | [runner](../sqlite_fragment_die_test1_tablefile_pageflow_fileparallel_fullscan.sh), [C source](../sqlite_append_die_affinity_tablefile_pageflow_fileparallel.c) | Separate files, scans/random-row modes and concurrent append |
| Filebench | [die runner](../fileserver_die_variants.sh), [heat runner](../fileserver_heat_variants.sh) | File-server access, heat and file-tier experiments |
| Synthetic layout | `hypothetical/`, `alignment_*.sh`, `pseudo_*.sh` | Historical layout/fragmentation experiments |

Root runners expect `commonvariables.sh` and sibling `../nvmevirt_DA/`; SQLite C sources are expected in `sqlite/`. The [workspace tool](../tools/prepare_workspace.py) creates that layout without moving tracked sources.

Set the staged [commonvariables.sh](commonvariables.sh) for the experiment machine. Inspect module-loading, partitioning and formatting before selecting a device workload. Archive analysis requires none of those operations.

Historical `HYBRID_TEST_GUIDE.md`, `MANUAL_TEST_GUIDE.md`, `QUICK_MANUAL_STEPS.md` and `MANUAL_TESTING_SUMMARY.md` are retained as context. Their examples/expected values do not replace the raw evidence or validation of a fresh run.
