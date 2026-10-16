# SLC/QLC 混合 SSD：放置策略与读延迟实验

[English](README.md) · [代码分类](docs/CODE_MAP.md) · [变体目录](docs/VARIANTS.md) · [结果来源](results/README.md) · [Linux 实验指南](docs/RUNBOOK.md)

本项目在 NVMeVirt 上研究文件布局、闪存放置策略和后台维护任务对读性能的影响，包含三组实验：

- **跨 die 放置：**观察文件碎片化后的页面分布，包含 die-affinity 和链式聚合变体。
- **SLC/QLC 页面管理：**按访问热度研究迁移、QLC 冷热放置、读晋升与重平衡。
- **后台维护调度：**比较 V1、V2、V3 策略，检查迁移和垃圾回收是否阻塞前台读取。

仓库包含内核模块、SQLite/fio/Filebench 负载、机制计数器及本地历史实验记录。NVMeVirt 将设备数据存放在预留的主机 DRAM 中并模拟闪存时延，下面的结果均属于这一仿真环境。

## 目录分类

| 分类 | 位置 | 内容 |
| --- | --- | --- |
| 模块基础代码 | [nvmevirt_DA/](nvmevirt_DA/) | 完整模块、Makefile、PCI/DMA 支持及继承文档 |
| FTL 与时序变体 | 根目录 `conv_ftl*`、`ssd*`、`channel_model*` 等 | 较新的实验实现，见[代码索引](docs/CODE_MAP.md) |
| 工作负载 | 根目录 `sqlite_*`、`fio_*`、`fileserver_*`；[evaluation/](evaluation/README.md) | 负载源码、驱动和历史脚本 |
| 技术文档 | [docs/](docs/) | 架构、变体选择与 Linux 环境 |
| 原始记录 | [results/raw/](results/raw/) | 4 份 fio JSON、4 份计数器快照、29 份 SQLite 日志 |
| 分析结果 | [results/derived/](results/derived/)、[results/figures/](results/figures/) | 重新生成的表格、诊断清单和图片 |
| 工具 | [tools/](tools/) | 文件校验、复算、绘图与工作区组装 |

当前源码由两个历史目录组成：根目录保存较新的覆盖文件，`nvmevirt_DA/` 保存完整模块和 Makefile。原始路径继续保留；组装工具会生成相邻的模块、负载目录，并将消融实验的编译参数接入 Kbuild。

## 不编译内核也能核对结果

表格复算和测试需要 Python 3.9+，无需额外 Python 包：

```bash
python3 tools/reproduce_results.py --check
python3 test_analyze_latency_run.py
python3 tools/test_artifact_tools.py
```

重新生成表格和图片：

```bash
python3 tools/reproduce_results.py
python3 -m venv .venv
.venv/bin/pip install -r tools/requirements-results.txt
.venv/bin/python tools/plot_results.py
```

以上命令只分析归档文件，不访问块设备。

## 已归档的 fio 结果

四份 `fio-3.16` 记录均采用 4 KiB I/O、`libaio`、直接 I/O、队列深度 32 和 Zipf 0.75。每份完成 **8 GiB 初始化写入、40 GiB 交错预热读取、5 GiB 正式读取及 0.5 GiB 正式写入**，fio 任务错误数均为 0。读写任务使用不同地址区域。

| 变体 | 读 IOPS | 读 p99（ms） | 读 p99.9（ms） | 读 p99.99（ms） |
| --- | ---: | ---: | ---: | ---: |
| V1 基础放置（`die_latency1_norp_sb`） | 16,704.518 | 7.307 | 170.918 | 1,019.216 |
| V1 冷热放置（`die_latency1_qlc_all_norp_sb`） | 17,257.670 | 7.307 | 227.541 | 1,052.770 |
| V2 冷热放置（`die_latency2_qlc_all_norp_sb`） | 19,863.005 | 6.390 | 132.645 | 616.563 |
| V3 冷热放置（`die_latency3_qlc_all_norp_sb`） | 18,899.255 | 6.586 | 233.832 | 566.231 |

![fio 读完成时延](results/figures/fio_read_latency.svg)

对应的[完整表格与原始文件链接](results/derived/SUMMARY.md)、[CSV](results/derived/fio_summary.csv)、[SHA-256 清单](results/manifest.json)均已提供。

每个变体只有一次运行。记录中的负载配置一致，但缺少匹配随机种子的实验协议、模块哈希和完整机器配置。现存 fio 快照中，V2/V3 的读优先控制计数器均为 0，不能将时延差异直接归因于调度机制。此组比较关闭了读晋升与 QLC 重平衡，名称中含 `qlc_all_norp` 的变体也如此。

SQLite 日志作为诊断资料保留：其中包含不同操作类型、缺失的时延统计和不完整的预热证据。每次运行都附有解析器判断，暂不将耗时差异写成已验证的加速结论。详见[结果来源与限制](results/README.md)。

## 准备 Linux 实验工作区

```bash
python3 tools/prepare_workspace.py --output .workspaces/nvmevirt
cd .workspaces/nvmevirt/nvmevirt_DA
bash build_die.sh die_i_base die_i_all
```

最后一步需要 Linux、兼容的 die-affinity 内核及对应头文件。工作区组装已测试；本次整理没有重新编译内核或运行设备负载。

运行前请阅读 [Linux 实验指南](docs/RUNBOOK.md)，按实验机器修改组装后的 `evaluation/commonvariables.sh`。历史脚本会装卸模块，部分会格式化指定设备；设备名来自旧配置，不会自动识别实验盘。

## 架构与变体

请求经 NVMe 处理路径进入选定的 FTL；页面放置和迁移/GC 任务共同使用 die、通道时序模型，最终通过日志和计数器核对行为。对应关系见[架构说明](docs/ARCHITECTURE.md)与[变体目录](docs/VARIANTS.md)。解释历史结果时，需要结合编译参数、运行配置和实际计数器。

## 来源与许可

基于 [NVMeVirt](https://github.com/snu-csl/nvmevirt)，继承的 die-affinity 说明和内核要求保存在 [nvmevirt_DA/README.md](nvmevirt_DA/README.md)。模块沿用 [GPL-2.0](LICENSE)，优先队列保留其[原有许可](nvmevirt_DA/pqueue/LICENSE)。

`Dac25_ae` 是仓库名称；本文未声明会议录用或制品认证。旧说明中的预期性能示例已由可追溯的历史实验记录替换。
