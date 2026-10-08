# 单只 ETF 量价研究

首期完成 P0：基本数据检查 → 14 个特征 → 条件收益 → 统计检验 → 成交量增量候选。
使用单只 ETF 日线，不使用机器学习，不自动生成或注册交易策略。
完整规则和停止条件见 [研究协议](protocol.md)。

## 安装与运行

使用 CPython 3.12、`conda activate xqat`，先按仓库说明安装现有锁定依赖及 editable 包。
从仓库根目录安装项目依赖：

```bash
python -m pip install --require-hashes -r projects/etf_price_volume/requirements.lock
python projects/etf_price_volume/run.py plan --config projects/etf_price_volume/configs/research.toml
python projects/etf_price_volume/run.py run --config projects/etf_price_volume/configs/research.toml --output .local/etf-price-volume/p0
```

项目锁文件包含已下载 wheel 的 SHA-256，目标 Linux x86_64 / CPython 3.12。
Windows 可运行工具，但需在该平台生成对应 wheel 哈希锁文件后安装。
NumPy 与仓库锁文件固定版本一致；研究依赖不进入主 CLI 依赖。

中断后用同一命令添加 `--resume`。输出已存在时不覆盖；新的配置或协议使用新的输出目录。
各阶段原子发布，恢复校验结果哈希。统计阶段未完成时重新执行该阶段，已完成阶段复用。
同一输出只允许一个协调进程；不同输出可独立并行。
输入、配置及项目源码路径与输出必须互不重叠。

## 配置与数据

[research.toml](configs/research.toml) 默认读取现有 `data/research/staged-etf`，证券为 510300.SH。
输入路径相对仓库根目录解析，输出路径相对当前工作目录。
默认开发期 2013–2023，2024 年起保留；上市后开发期之前的数据用于预热。
只检查原始输入，不联网。现有数据足够时不重新下载，也不删除旧数据。
Manifest 与实际日期范围差异记录为提示；重复、缺失开市日、无效 OHLC 等基本错误阻止研究。

`workers=16` 控制统计检验进程数，可以按 CPU/内存修改；底层数值库每进程单线程。
默认 300 项预登记检验，每项 2000 次区块 Bootstrap；确定的任务种子不受进程数影响。
配置中的样本门槛、检验期限和随机种子可调整，但任何调整都保留为新实验。

## 输出

根目录 `report.md` 是阅读入口，`manifest.json` 记录来源、代码、依赖和阶段哈希。
`data/` 保存预热与开发期标准行情和简要质量说明；不含保留期 OHLCV。
`features/`、`labels/` 保存 Parquet、定义及排除原因。
`conditional/` 包含五分组、3×3 联合收益、年度与五年滚动统计、分布和相关性。
`statistics/` 保存全部检验、置信区间、FDR 与未通过原因，另存 `candidates.json`。
`report/` 包含 PNG 分布、相关性及量价热力图和 Markdown 报告。

首期结果使用复权价格收益代理，未确认实际分红、拆分成交量口径与交易成本。
单特征有效不等于成交量有增量；候选不等于可交易优势；没有通过者也是完整研究结论。
2024 年后的行情此前已用于其他研究，因此该保留集不能被称为完全独立验证。

## 验证

```bash
python -m pytest projects/etf_price_volume/tests -q
python -m ruff check projects/etf_price_volume
```

后续仅按证据进入候选交易规则、纯价格消融、网格搜索、连续滚动验证和成本稳健性。
这些研究工具留在本项目目录；正式策略、现金计息和连续回测扩展遵循仓库核心边界。
