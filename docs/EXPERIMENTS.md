# 完整消融、重复实验与调度追踪

这套入口生成新实验数据；历史37个原始文件保持原样。所有新设备结果仍为 **DATA REQUIRED**，直到兼容的Linux实验机实际运行并通过校验。CI中的编译和用户态fixture不等于加载模块或跑过设备。

## 1. 配置实验机

需要Python 3.10+、fio **3.16**（libaio）、GCC/make、sudo/util-linux、匹配运行内核的headers，以及仓库要求的die-affinity内核环境。若只做预览/解析测试，不需要root或块设备。

在本仓库的干净、已提交版本中：

    cp experiments/host_config.example.sh experiments/host_config.sh

填写该文件中的绝对路径、实际模拟namespace名称、已保留物理内存起点、模拟器CPU及host fio CPU。host与模拟器CPU必须不重叠。配置文件默认忽略，不提交机器信息。

- 介质模型固定4GiB SLC+60GiB QLC、2channels×2LUN/channel×1plane，共4dies。
- 保留内存长度须65537MiB：64GiB数据介质加1MiB元数据前缀。起点由实验机器实际保留区决定；填写地址不会自动保留内存。
- OP7%和512B LBA对齐后，预期逻辑namespace为64,223,809,536B，约59.81GiB。程序分别核对保留、物理模型和逻辑容量。
- 使用专用模拟设备；运行前检查model/serial、挂载和swap。fio将覆盖所选地址区域。
- 不调用旧的setdevice.sh。该脚本会处理DATA和JOURNAL两块盘，属于SQLite/文件系统路径。

先保存并核对uname、/proc/cmdline、/proc/iomem、lscpu/NUMA、fio版本。物理保留区仍需实验人员核对；入口不会修改启动参数或重启。

## 2. 准备、预览和构建

从仓库根目录执行：

    source experiments/host_config.sh
    python3 experiments/protocol.py prepare --repo . \
      --workspace "$FW_WORKSPACE" --device-name "$FW_DEVICE_NAME"
    bash experiments/run_matrix.sh preview experiments/host_config.sh "$FW_RESULTS/preview_v2"
    bash experiments/build_all.sh "$FW_WORKSPACE" "$FW_RESULTS/build_v2"

prepare创建新workspace，使用仓库的tools/prepare_workspace.py装配模块和负载，并只在staged副本修补几何、设备、loader、种子/JSON+/阶段同步和严格检查。原仓库runner保留历史调用方式，新实验统一走experiments入口。

正式prepare冻结当前Git commit、运行辅助文件及全部staged输入hash，要求源码干净。仅供开发的--allow-dirty-preview会永久把该workspace标为不可执行，不能用于正式run。更新代码、flags或负载后创建新workspace/协议版本。

build_all构建四个模块并绑定protocol_inputs.json、编译flags、headers、compiler和四个.ko哈希。每次run核对这个构建记录、所选文件和已加载srcversion；不接受未绑定来源的旧.ko。只存在headers不代表模块可加载或内核兼容。

| Cell | 放置P | 调度家族S | build_die.sh变体 |
| --- | --- | --- | --- |
| A | off | V1 | die_latency1_norp_sb |
| B | on | V1 | die_latency1_qlc_all_norp_sb |
| C | off | V3 | die_latency3_norp_sb |
| D | on | V3 | die_latency3_qlc_all_norp_sb |

四组关闭repromotion、die-batched repromotion、rebalance及对应test-phase开关；统一V3 requeue/force/gate参数和诊断编译宏。V1/V3是调度家族组合变化，不能解释成只切换yield一项。test_phase=0也不能用作scheduler-off对照。

normal时序、gc_nand_timing=1、recent_write_guard=1、guard_read_reqs=256会被设置并回读。名义requeue=0仍最低一jiffy，保存CONFIG_HZ。

## 3. 先pilot，再跑20次正式实验

源码级时间函数探针不访问设备：

    python3 experiments/mechanism_probe.py --repo . --out "$FW_RESULTS/timing_probe_v2"

它逐字提取当前已提交版本的纯C时间函数。已知模型边界：优先读可越过已有低优先级reservation；没有in-flight状态，因此不能从这些函数声称实际NAND非抢占。探针报告事实和缺口，不生成性能结果。

设备pilot：四组各一次，seed9001，每组重新load/init：

    sudo bash experiments/run_matrix.sh pilot experiments/host_config.sh "$FW_RESULTS/pilot_v2"

核对四组模块/几何、完整I/O、阶段同步、内核异常及维护触发。没有yield不丢弃结果；若需要更强压力，另开profile=pressure诊断集，不替换主结果。pilot不并入正式五个seed。

正式执行：

    sudo bash experiments/run_matrix.sh formal experiments/host_config.sh "$FW_RESULTS/formal_attempt1"

固定顺序见[run_matrix.csv](../experiments/run_matrix.csv)：

| seed | 顺序 |
| --- | --- |
| 101 | A B D C |
| 202 | B C A D |
| 303 | C D B A |
| 404 | D A C B |
| 505 | A B D C |

前四block平衡，第五为追加block，不能称全部五block完全平衡。运行串行并持有全局module lease；每cell独立初始化。任一阶段失败即停止，失败目录保留。先查现场，确认模块无人使用后再决定卸载；不要自动清空证据。重试用新attempt，不覆盖旧输出。

主负载：

- 4KiB、libaio/direct、固定体量；1读job+1写job，每job QD32、numjobs1。
- 读地址[0,8GiB)，写地址[8,8.5GiB)，写跨度512MiB。
- 初始化8GiB，16个512MiB chunk；每chunk5倍预热，共40GiB预热读。
- 49个4KiB阶段标记读单独保留，共196KiB，不计入测量读。
- 测量5GiB读+512MiB写，Zipf0.75；10:1是最终字节量比，不是恒定瞬时IOPS比。
- 每job seed=block seed+原始零基job索引；同seed四cell一致。该新协议不是旧归档随机流的精确重放。
- 外层同步执行82个阶段/83jobs，测量读写处于最后同一次fio调用。实际交给fio的文件无exec_*钩子，baseline失败不会进入测量。

JSON+ completion bins和请求数/字节守恒均须通过。每秒lat平均日志不能用来画请求级CDF。

完整批次可生成描述性配对表：

    python3 experiments/summarize.py --batch "$FW_RESULTS/formal_attempt1" \
      --out "$FW_RESULTS/formal_attempt1_analysis"

输出逐run值、每seed的B/A、C/A、D/A、D/B、D/C及乘法交互，以及五seed描述性比值汇总。主指标预先固定read P99.9，展示mean/P99/P99.99/IOPS及写尾延迟。缺失保持NA。seed/run是重复单位；五seed不自动支持显著性结论。run-level P99比值汇总不是合并I/O的P99。

## 4. 调度追踪：独立诊断运行

默认trace_mode=off。只有显式diag运行才建立独立tracefs instance，启用flashweave的三个事件。诊断数据不能混入20次性能批次。

示例（已有构建和有效机器配置）：

    source experiments/host_config.sh
    sudo python3 experiments/protocol.py run --workspace "$FW_WORKSPACE" \
      --cell D --seed 9002 --profile pressure --trace-mode diag \
      --out "$FW_RESULTS/diag_seed9002_D" \
      --memmap-start "$FW_MEMMAP_START" --cpus "$FW_CPUS" --host-cpus "$FW_HOST_CPUS"

将cell改为B/C/A并改新目录可形成同开销诊断对照。具体追踪字段和CLI见[TRACE_FORMAT.md](TRACE_FORMAT.md)。

记录的是实际源码调用时的调度决策和**模型时间预约**，不是物理NAND issue/completion采样。normal channel/PCIe credit模型内部不可见的start不会伪造。没有可靠host-parent关联的字段明确不可用。

逐op文本可能产生数GiB数据，默认上限8GiB，可用--trace-max-bytes调整；正式20次关闭逐op追踪。

capture保存实际event format、mono时钟、流式trace、逐CPU overrun/drop计数及失败状态；容量/时长上限、缺统计、漏事件或空追踪均不能作为完整机制证据。禁止事后只保留好看的trace窗口。

## 5. 每run的数据与验收边界

保存manifest、完整生成job/phase plan与逐job seed、源/执行辅助文件/build/模块hash、fio二进制/version、控制readback、CPU/NUMA/内核/内存映射、stdout/stderr、scoped kernel日志、原始JSON+及hist/bw/iops、失败状态和SHA256。

| 边界 | 定义 | 当前状态 |
| --- | --- | --- |
| I | 模块已加载、控制已核对、初始化前 | preinit快照 |
| B | init/prewarm/marker完成，外层reset/核对后、测量前 | baseline快照 |
| F | 两个测量job完成，等待前 | raw foreground sidecars |
| Q | 至少60s，active=0且选定计数3×5s不变；上限600s | statistical stationarity |
| D | pending/delayed/running work及可执行maintenance debt退休，时钟超过所有reservation completion | **DATA REQUIRED：未提供全模型drain证明** |

计算F-B、Q-B、Q-F。reset不清placement、heat或reservation时间；跨接口快照不是原子事务，保存采集时间。后台计数可在B采集期间增长，必须使用实际B值。

internal_write_pages_est是估计内部page口径，不能直接命名完整设备WAF。die busy_ns可能含等待，不能直接当物理利用率。缺字段记NA，不填0。

check会拒绝明确的kernel BUG/Oops、KASAN/UBSAN、CPU warning或hung-task等故障记录。通过只表示这些协议/体量/分布门槛通过，仍需核对scoped kernel log中的WARN/BUG/Oops/timeout/mismatch；trace存在、yield非零也不自动证明延迟收益或维护完全排空。

## 6. 仍缺的数据和机制验证

| 项目 | 接口/执行入口 | 缺口标记 |
| --- | --- | --- |
| 四cell×五seed | protocol + run_matrix + summarize | DATA REQUIRED：实际运行 |
| pending maintenance让读先行 | worker/NAND/resource trace | DATA REQUIRED：同事件集/可重复触发与实际诊断 |
| 已开始维护负控 | 源函数probe + reservation trace | DATA REQUIRED：完整FTL in-flight边界；不得声称硬件非抢占 |
| 不同die隔离 | NAND/resource trace | DATA REQUIRED：完整路径含共享channel/PCIe验证 |
| 8次yield后强制进展、紧急压力 | worker decision/pass和实际moved_pages（全口径retired不可用） | DATA REQUIRED：持续读/压力场景实测 |
| 全模型维护排空 | 现有Q快照 | DATA REQUIRED：pending/debt/latest completion接口与协议 |
| 热页正确率、动态heat/relocation | page_tier_raw/page_die/access_count | DATA REQUIRED：独立heat真值、单开关矩阵和实测 |
| SQLite/IMDb | 原SQLite脚本仅供诊断 | DATA REQUIRED：全局种子、实际ROW/NAND/heat、结束前F/Q、输入来源和样本 |

新增追踪不会改变调度策略或修补已知的reservation模型边界。若以后改模型或加入纯yield开关，提升协议版本并重新完成pilot及整套重复。

## 7. 无设备验证

    python3 tools/reproduce_results.py --check
    python3 test_analyze_latency_run.py
    python3 tools/test_artifact_tools.py
    python3 -m unittest discover -s experiments -p 'test_*.py' -v
    python3 -m unittest discover -s tests -p 'test_*.py' -v
    python3 -m unittest discover -s experiments/tests -p 'test_*.py' -v

Linux可另做纯编译检查：

    KERNELDIR=/path/to/installed/kernel/headers bash tools/check_kernel_build.sh

CI的distribution-header编译只检查模块源码/链接兼容性，不证明历史修改内核或设备行为。实际设备实验仍需兼容环境、有效保留内存和填写后的host config。原始结果的完整性校验与新设备实验验收分别保留。

