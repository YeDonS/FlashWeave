# Variant catalog / 变体目录

The authoritative build mapping is `ftl_source_for()` in [build_die.sh](../build_die.sh). All selected variants use `ssd_die.c`. The list below includes aliases; an alias is not a separate implementation.

## Families

| Family | Build names | Purpose |
| --- | --- | --- |
| Integrated ablations | `die_i_base`, `die_i_all`, `die_i_no_*`, `die_i_chain_only`, `die_i_no2_only`, `die_i_no3_only` | One `conv_ftl.c` with feature flags from `variant_flags_for()` |
| Historical baseline | `die_base`, `die_base_sb`, `die_base_lru`, `die_base2`, `die_base3` | Baseline, superblock and mapping-cache experiments |
| Legacy feature sets | `die_all`, `die_no1`–`die_no4`, `die_*_sb` | Earlier feature combinations; inspect source and build flags |
| Latency core | `die_latency1_sb`, `die_latency2_sb`, `die_latency3_sb` | V1 maintenance / V2 per-die idle-demand scheduler / V3 foreground-read priority |
| Wrapper ablations | `die_latency[123]_{norp,qlc_hotcold,qlc_all,qlc_all_norp}_sb` | Scheduler core plus explicitly selected placement/maintenance features |

## Wrapper feature meanings

| Suffix | QLC hot/cold | Read repromotion | QLC rebalance |
| --- | ---: | ---: | ---: |
| `norp` | Off | Off | Off |
| `qlc_hotcold` | On | Inherited core settings; inspect source/runtime | Off |
| `qlc_all` | On | Inherited core settings; inspect source/runtime | On |
| `qlc_all_norp` | On | Off | Off |

`qlc_all_norp` is a historical label: despite `all`, its wrapper disables rebalance as well as repromotion. The four-run fio archive uses `norp` and `qlc_all_norp` families. Runtime snapshots, not names alone, establish the recorded feature state.

Integrated flags must reach Kbuild. Use the prepared workspace: its Makefile consumes `NVMEV_EXTRA_CFLAGS`. The original inherited Makefile does not. Runtime controls and active mechanism counters still need verification after compilation.

## Exact build-name mapping

| Build name | FTL source |
| --- | --- |
| `die_all` | [conv_ftl.c](../conv_ftl.c) |
| `die_no1` | [conv_ftl_no1.c](../conv_ftl_no1.c) |
| `die_no1_sb` | [conv_ftl_no1_superblock.c](../conv_ftl_no1_superblock.c) |
| `die_no2` | [conv_ftl_no2.c](../conv_ftl_no2.c) |
| `die_no3` | [conv_ftl_no3.c](../conv_ftl_no3.c) |
| `die_base` | [conv_ftl_baseline.c](../conv_ftl_baseline.c) |
| `die_base_sb` | [conv_ftl_baseline_superblock.c](../conv_ftl_baseline_superblock.c) |
| `die_latency1_sb` | [conv_ftl_latency1_superblock.c](../conv_ftl_latency1_superblock.c) |
| `die_latency_sb_v1` | [conv_ftl_latency1_superblock.c](../conv_ftl_latency1_superblock.c) |
| `die_latency2_sb` | [conv_ftl_latency2_superblock.c](../conv_ftl_latency2_superblock.c) |
| `die_latency_sb` | [conv_ftl_latency2_superblock.c](../conv_ftl_latency2_superblock.c) |
| `die_latency3_sb` | [conv_ftl_latency3_superblock.c](../conv_ftl_latency3_superblock.c) |
| `die_latency1_norp_sb` | [conv_ftl_latency1_norp_superblock.c](../conv_ftl_latency1_norp_superblock.c) |
| `die_latency2_norp_sb` | [conv_ftl_latency2_norp_superblock.c](../conv_ftl_latency2_norp_superblock.c) |
| `die_latency3_norp_sb` | [conv_ftl_latency3_norp_superblock.c](../conv_ftl_latency3_norp_superblock.c) |
| `die_latency1_qlc_hotcold_sb` | [conv_ftl_latency1_qlc_hotcold_superblock.c](../conv_ftl_latency1_qlc_hotcold_superblock.c) |
| `die_latency2_qlc_hotcold_sb` | [conv_ftl_latency2_qlc_hotcold_superblock.c](../conv_ftl_latency2_qlc_hotcold_superblock.c) |
| `die_latency3_qlc_hotcold_sb` | [conv_ftl_latency3_qlc_hotcold_superblock.c](../conv_ftl_latency3_qlc_hotcold_superblock.c) |
| `die_latency1_qlc_all_sb` | [conv_ftl_latency1_qlc_all_superblock.c](../conv_ftl_latency1_qlc_all_superblock.c) |
| `die_latency2_qlc_all_sb` | [conv_ftl_latency2_qlc_all_superblock.c](../conv_ftl_latency2_qlc_all_superblock.c) |
| `die_latency3_qlc_all_sb` | [conv_ftl_latency3_qlc_all_superblock.c](../conv_ftl_latency3_qlc_all_superblock.c) |
| `die_latency1_qlc_all_norp_sb` | [conv_ftl_latency1_qlc_all_norp_superblock.c](../conv_ftl_latency1_qlc_all_norp_superblock.c) |
| `die_latency2_qlc_all_norp_sb` | [conv_ftl_latency2_qlc_all_norp_superblock.c](../conv_ftl_latency2_qlc_all_norp_superblock.c) |
| `die_latency3_qlc_all_norp_sb` | [conv_ftl_latency3_qlc_all_norp_superblock.c](../conv_ftl_latency3_qlc_all_norp_superblock.c) |
| `die_all_sb` | [conv_ftl_all_superblock.c](../conv_ftl_all_superblock.c) |
| `die_no2_sb` | [conv_ftl_no2_superblock.c](../conv_ftl_no2_superblock.c) |
| `die_base_lru` | [conv_ftl_baseline_lru.c](../conv_ftl_baseline_lru.c) |
| `die_no4` | [conv_ftl_no4.c](../conv_ftl_no4.c) |
| `die_base2` | [conv_ftl_base2.c](../conv_ftl_base2.c) |
| `die_base3` | [conv_ftl_base3.c](../conv_ftl_base3.c) |
| `die_i_base` | [conv_ftl.c](../conv_ftl.c) |
| `die_i_all` | [conv_ftl.c](../conv_ftl.c) |
| `die_i_no_chain` | [conv_ftl.c](../conv_ftl.c) |
| `die_i_no_hotcold` | [conv_ftl.c](../conv_ftl.c) |
| `die_i_no_repromote` | [conv_ftl.c](../conv_ftl.c) |
| `die_i_no_rebalance` | [conv_ftl.c](../conv_ftl.c) |
| `die_i_chain_only` | [conv_ftl.c](../conv_ftl.c) |
| `die_i_no2_only` | [conv_ftl.c](../conv_ftl.c) |
| `die_i_no3_only` | [conv_ftl.c](../conv_ftl.c) |

## Selection groups

`build_die.sh ablations` builds the integrated ablation group. `all` builds the script's complete selection. Check the group arrays in the script before launching a large build. Prefer a small, explicitly named comparison when changing one mechanism.

Not every `.c` snapshot is mapped by this builder. `conv_ftl_nd.c` and `conv_ftl_latency_superblock.c` are retained historical files, not automatically selected fallback implementations.
