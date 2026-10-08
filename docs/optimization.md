# 离线参数优化

`xqatexp backtest opt` 使用本地 Research Artifact 批量回测候选参数。普通回测配置
提供策略、数据和执行假设，独立优化配置提供搜索空间、目标、约束和并行设置。
每个试验调用已有回测工作流，产生标准 Result Artifact，不访问网络。

## 分级 ETF 搜索

从仓库根目录执行：

```bash
xqatexp backtest opt --config examples/config-staged-etf.toml --opt-config examples/config-staged-etf-opt.toml --output .local/staged-etf-backtest-opt --start-date 2013-01-01 --existing overwrite
```

示例优化配置固定 `entry_confirmation_mode = "ma_rebound"`，否则均线和确认窗口
不会影响入场。其余策略参数、止盈卖出比例、初始资金、费用和滑点继承普通配置；
未配置的字段仍由 Strategy Registry 使用现有默认值。两个输入 TOML 均不修改。

搜索范围：

| 参数 | 候选值 |
| --- | --- |
| entry_confirmation_ma_days | 2、3、5、8、10、15、20 |
| entry_confirmation_window_days | 0、3、5、10、15、20 |
| take_profit_levels 第一档 | 0.05、0.10、0.15 |
| take_profit_levels 第二档 | 0.10、0.20、0.30 |
| take_profit_levels 第三档 | 0.20、0.30、0.50 |

三档必须严格递增。27 个数组候选过滤后剩下 12 个，共 **7 × 6 × 12 = 504**
个独立参数组合，包含启用确认后的原参数组合。另运行一次原配置对照，共 505 次
回测；对照不参加优化排名。

日期优先级为 CLI > 优化配置 > 普通配置。示例优化配置结束于 `2024-01-01`，
因此上述命令不会使用普通配置的 `2026-09-01` 作为训练终点。实际估值日期取区间内
交易日；预热可以读取训练起点之前的数据，但不计入绩效，不扩展训练终点。
输入必须包含起点需要的完整预热历史；预检读取真实表内容，不只依赖 Manifest 日期。

## 优化 TOML

参见 [示例配置](../examples/config-staged-etf-opt.toml)。支持的字段：

- `schema_version = "1.0"`、`method = "grid"`；目前完整遍历离散空间。
- `start_date`、`end_date`、`workers`；可由 CLI 日期与 `--workers` 覆盖。
- `[objective]`：`metric` 和 `direction`，方向为 `maximize` 或 `minimize`。
- `[[constraints]]`：`metric`、`operator`、有限数值 `value`，操作符支持 `>`、`>=`、`<`、`<=`、`==`。
- `[fixed_strategy]`：显式覆盖普通配置的策略字段。
- `[parameters]`：字段名到非空候选数组，数组值也可作为完整分类候选。
- `[ordered_arrays]`：字段名到各位置的候选数组，生成严格递增的数值数组。

搜索字段与固定覆盖项不能重叠。全部候选经 Registry 校验后按完整规范化参数哈希
去重；非法策略组合在回测前报错。分级 ETF 示例要求普通配置已启用三档 `tiered`
止盈，其卖出比例由普通配置提供。

示例目标为满足配置中成交记录数和年化收益门槛的最高夏普率，实际条件以输入
TOML 为准；年化收益 `0.01` 表示 1%。
BUY 和 SELL 的实际成交记录都计数，不把交易次数解释为完整往返次数。
收益、回撤和夏普率读取现有 `metrics.json`；空值或非有限目标不合格。
同目标值按年化收益降序、回撤绝对值升序、参数哈希升序确定排名。

## 并行、预检与续跑

默认 16 个独立工作进程，使用 `spawn`；每个进程的 DuckDB 最多四个线程，
PyArrow 工作进程的 CPU/IO 线程各一个。通过 CLI 调整进程数：

```bash
xqatexp backtest opt --config examples/config-staged-etf.toml --opt-config examples/config-staged-etf-opt.toml --output .local/staged-etf-backtest-opt --workers 32 --dry-run
xqatexp backtest opt --config examples/config-staged-etf.toml --opt-config examples/config-staged-etf-opt.toml --output .local/staged-etf-backtest-opt --workers 32 --resume
```

`--dry-run` 校验数据、预热、参数和路径，打印最终日期、候选数量与覆盖项，
不运行回测，不创建输出目录、锁或检查点。对照回测耗时用于给出粗略运行时间估计。

输出策略作用于整个实验：

- `--existing error`：默认拒绝已有输出。
- `--existing skip`：仅跳过输入一致、完整且校验通过的实验；不续跑部分结果。
- `--existing overwrite`：从头重跑，旧目录保留为旁边的备份，替代实验成功后清理。
- `--resume`：校验输入与检查点，恢复未完成或失败组合。不能同时使用 skip/overwrite。

失败或中断保留完成的 Artifact 与检查点；overwrite 的旧备份也会保留。
Ctrl+C 返回 130。续跑会恢复已发布但尚未写入检查点的试验；损坏或缺失的已完成
Artifact 不自动覆盖。修改策略配置、优化配置、代码或研究输入会拒绝续跑。
改变并行数请使用 `--workers`，保持优化 TOML 内容一致。

一个输出目录只能有一个协调进程。不同目录可同时运行独立实验。

## 分片与合并

可将相同网格分配给独立实验，分片索引从 0 开始：

```bash
xqatexp backtest opt --config examples/config-staged-etf.toml --opt-config examples/config-staged-etf-opt.toml --output .local/etf-opt-0 --workers 16 --shard-count 2 --shard-index 0
xqatexp backtest opt --config examples/config-staged-etf.toml --opt-config examples/config-staged-etf-opt.toml --output .local/etf-opt-1 --workers 16 --shard-count 2 --shard-index 1
xqatexp backtest opt-report --input .local/etf-opt-0 --input .local/etf-opt-1 --output .local/etf-opt-combined
```

候选按哈希稳定排序后取模分片，同一网格不会漏选或重复分配；各分片单独运行对照。
合并验证训练日期、数据、代码、固定参数、执行假设、搜索字段、目标和约束兼容，
并验证试验 Artifact。同参数去重；指标冲突拒绝合并。报告记录来源路径、Manifest
哈希、各来源候选范围、总记录数和唯一记录数。

## 不重跑回测，重新筛选

完整搜索结束后，修改约束或目标无需重新运行 `backtest opt`。从来源实验的
`input-opt-config.toml` 复制新配置，保持日期、固定策略和候选空间一致，只修改
`[objective]`、`[[constraints]]`；workers 可以变化但不会使用。例如保留成交次数
`> 2`，将年化收益门槛从 `0.01` 改为 `0.008`（0.8%）：

匹配当前 2020–2024 示例网格的完整配置见
[放宽条件示例](../examples/config-staged-etf-opt-relaxed.toml)。其他实验应从来源快照复制。

```bash
cp .local/staged-etf-backtest-opt/input-opt-config.toml .local/staged-etf-opt-relaxed.toml
# 编辑 .local/staged-etf-opt-relaxed.toml 中的约束，再预检和发布：
xqatexp backtest opt-report --input .local/staged-etf-backtest-opt --opt-config .local/staged-etf-opt-relaxed.toml --output .local/staged-etf-backtest-opt-relaxed --dry-run
xqatexp backtest opt-report --input .local/staged-etf-backtest-opt --opt-config .local/staged-etf-opt-relaxed.toml --output .local/staged-etf-backtest-opt-relaxed
```

提供 `--opt-config` 时只接受一个完整、无失败的原始优化实验，不能使用单个分片、
合并目录或复筛目录。读取已保存的实际配置，保留原 CLI 日期覆盖，无需 `--config`。
原 Research Manifest 和自定义因子仍须存在且指纹匹配；全部试验、基准和检查点
经过校验后，重新判断原合格及不合格组合。目标或约束可增加、删除和修改，但不会
计算新指标、补跑组合或改变训练区间。原回测代码指纹保留，不与当前报告代码比较。

新目录输出 `results.csv`、`leaderboard.csv`、`report.md`、配置快照、`sources.json`
和 Manifest；有合格组合时生成 `best.json`、`best-config.toml`。CSV 的 `artifact_path`
列、最佳 JSON 和来源记录指向原始试验目录，不复制 Parquet，也不创建 `trials/`。
报告显示新旧条件及“新增回测次数: 0”。来源目录保持不变，移动或删除来源后需
自行处理原试验路径。再次调整约束时仍指定原始优化实验。

复筛沿用 `--existing error/skip/overwrite`，skip 要求来源及新筛选配置一致；覆盖前
先校验来源，发布失败保留旧输出备份。无合格组合仍成功返回 0，不生成最佳文件。
`--dry-run` 支持复筛及普通合并，不运行回测、不写目录、锁、运行日志或失败报告。

```bash
xqatexp backtest run --config .local/staged-etf-backtest-opt-relaxed/best-config.toml --output .local/staged-etf-relaxed-best-backtest
```

## 输出与最佳配置

```text
.local/staged-etf-backtest-opt/
├── study_manifest.json
├── input-config.toml
├── input-opt-config.toml
├── candidates.json
├── selected.json
├── checkpoint.json
├── baseline/
├── trials/<parameter-sha256>/
├── results.csv
├── leaderboard.csv
├── best.json
├── best-config.toml
└── report.md
```

`results.csv` 包含参数、指标、状态和失败/不合格原因。Markdown 包含对照、前十名和
均线/窗口组合的最佳合格止盈档位。没有合格组合时不生成最佳配置，也不放宽条件。
分片未合并、任务失败或任务未完成时，仅报告已完成组合中的最佳结果；全部 504
个组合成功后才标记完整网格完成。

最佳配置是完整的普通回测 TOML，可直接复现：

```bash
xqatexp backtest run --config .local/staged-etf-backtest-opt/best-config.toml --output .local/staged-etf-best-backtest
```

搜索结果目录是带检查点的实验记录，各试验目录是标准 Result Artifact。阅读实验
汇总使用 `report.md`；普通 `result show` 用于试验或最佳配置的回测结果。
搜索、合并成功返回 0；试验失败或合并覆盖不完整返回 5。无合格组合但全部试验
成功执行仍返回 0。其他输入、数据、发布错误沿用 CLI 对应退出码。

最佳值只代表设定训练区间与候选范围内的结果，不自动评价 2024 年之后的数据。
