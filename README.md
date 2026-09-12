# XQatExp

XQatExp 是一个本地运行的纯 Python A 股量化研究工具。它将显式的 Tushare 原始数据构建为不可变 Research Artifact，并使用同一个周频策略实现历史回测、每日目标和账户事实约束下的参考建议。策略只投资 A 股个股、沪深300ETF与现金，优先控制理论策略组合回撤。

## 1. 安装（Linux，首选）

正式支持 Linux x86_64 和 Windows 10/11 x64；Linux 优先。Linux 验收基线是 Ubuntu 22.04.5 LTS（WSL2、glibc 2.35），最低 Linux ABI 为 glibc 2.28 级别。要求 64 位 CPython 3.12；Ubuntu 22.04 自带的 Python 3.10 不作为运行时。在仓库根目录执行：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m pip install --require-hashes -r requirements-build.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation .
.venv/bin/xqatexp self-check --offline
```

`requirements-build.lock` 只锁定构建后端，开发与测试依赖使用 `requirements-dev.lock`。需要源码可编辑安装的开发者可把最后一条安装命令改为结尾带 `-e .`。

## 2. 安装（Windows PowerShell）

Windows 10/11 x64 同样正式支持，使用独立的 CPython 3.12 venv。在仓库根目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-build.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation .
.\.venv\Scripts\xqatexp.exe self-check --offline
```

Windows `.venv` 与 Linux `.venv` 不能共享或互用。若同一工作目录可从 Windows 与 WSL 的 `/mnt/c` 访问，WSL 不得使用 Windows 创建的 `.venv`。正式 Linux 验收必须在 WSL 原生 ext4 文件系统中的干净仓库副本和独立 `.venv` 中运行，避免 DrvFS 掩盖 POSIX 权限、rename 或符号链接问题。Git Bash 对应激活命令是 `source .venv/Scripts/activate`，也可以始终直接调用 `.venv/Scripts/python.exe`。

## 3. 完整离线示例

离线示例使用确定性的合成行情，不访问网络，也不需要 Token。Linux：

```bash
.venv/bin/python examples/generate_offline_example.py --root .example-work
.venv/bin/xqatexp data check-research --input .example-work/research --report .example-work/research-check.json
.venv/bin/xqatexp daily target --config examples/config-offline.toml --decision-date 2026-09-04 --output .example-work/daily-target
.venv/bin/xqatexp daily advise --config examples/config-offline.toml --target .example-work/daily-target --account examples/account-complete.json --output .example-work/daily-advice
.venv/bin/xqatexp backtest run --config examples/config-offline.toml --start-date 2026-08-31 --end-date 2026-09-07 --output .example-work/backtest
.venv/bin/xqatexp result show --input .example-work/backtest --format markdown
```

Windows PowerShell 对应命令：

```powershell
.\.venv\Scripts\python.exe examples\generate_offline_example.py --root .example-work
.\.venv\Scripts\xqatexp.exe data check-research --input .example-work\research --report .example-work\research-check.json
.\.venv\Scripts\xqatexp.exe daily target --config examples\config-offline.toml --decision-date 2026-09-04 --output .example-work\daily-target
.\.venv\Scripts\xqatexp.exe daily advise --config examples\config-offline.toml --target .example-work\daily-target --account examples\account-complete.json --output .example-work\daily-advice
.\.venv\Scripts\xqatexp.exe backtest run --config examples\config-offline.toml --start-date 2026-08-31 --end-date 2026-09-07 --output .example-work\backtest
.\.venv\Scripts\xqatexp.exe result show --input .example-work\backtest --format markdown
```

重复生成示例时给生成脚本加 `--overwrite`；重复写正式结果时，只有明确指定 `--existing overwrite` 才会替换已验证结果。

回测配置可在顶层加入 `analysis_periods = [{ label = "阶段名", start_date = "YYYY-MM-DD", end_date = "YYYY-MM-DD" }]`。阶段可重叠但必须位于回测范围内，结果写入 `period_metrics.csv`，并明确标记为 `USER_DEFINED`。

自定义因子路径，Linux：

```bash
.venv/bin/xqatexp factor check --file .example-work/custom-factor.csv --research .example-work/research --strategy weekly_market_guard_rank_v1 --start 2026-08-31 --end 2026-09-04 --report .example-work/factor-check.json
.venv/bin/xqatexp daily target --config examples/config-custom-factor.toml --decision-date 2026-09-04 --custom-factor .example-work/custom-factor.csv --output .example-work/custom-daily-target
```

Windows PowerShell 对应命令：

```powershell
.\.venv\Scripts\xqatexp.exe factor check --file .example-work\custom-factor.csv --research .example-work\research --strategy weekly_market_guard_rank_v1 --start 2026-08-31 --end 2026-09-04 --report .example-work\factor-check.json
.\.venv\Scripts\xqatexp.exe daily target --config examples\config-custom-factor.toml --decision-date 2026-09-04 --custom-factor .example-work\custom-factor.csv --output .example-work\custom-daily-target
```

`account-complete.json`、`account-empty.json`、`account-partial.json` 和 `account-unknown.json` 展示不同账户完整性。账户缺失、部分或未知时，目标权重仍保留，但系统不会臆造当前持仓或可执行数量。建议固定包含“仅供研究参考”的非订单声明。

## 4. Tushare 数据

Token 仅从当前进程的 `TUSHARE_TOKEN` 环境变量读取，不允许写入 TOML、JSON、源码或命令参数。请在本机临时设置自己的值。Linux：

```bash
export TUSHARE_TOKEN="<在本机填写你的 Token>"
.venv/bin/xqatexp data capabilities --output .local/tushare-capabilities.json
.venv/bin/python -m pytest -m live_tushare --live-tushare -q
.venv/bin/xqatexp data fetch --dataset stock_daily --start 2026-08-01 --end 2026-08-31 --output data/raw/stock-daily-202608
```

Windows PowerShell 的进程局部设置和对应命令：

```powershell
$env:TUSHARE_TOKEN = "<在本机填写你的 Token>"
.\.venv\Scripts\xqatexp.exe data capabilities --output .local\tushare-capabilities.json
.\.venv\Scripts\python.exe -m pytest -m live_tushare --live-tushare -q
.\.venv\Scripts\xqatexp.exe data fetch --dataset stock_daily --start 2026-08-01 --end 2026-08-31 --output data\raw\stock-daily-202608
```

最小真实接口测试需要显式开关，且只发起一次日线请求。能力探测会逐项报告当前积分可调用的接口。正式抓取每次只处理一个显式数据集和日期范围；随后对每个 Raw Artifact 执行 `data check-raw`，并把构建所需的各个 `--raw-root` 显式传给 `data build`。工具不会自动寻找 `latest`，也不会在回测、每日模式或结果查看时访问网络。

## 5. 产物与解释

需要保留最小运行事件时，把全局参数放在业务命令之前，例如 `xqatexp --log-file .local/run.jsonl backtest run ...`。日志为脱敏 JSON Lines，只记录命令阶段和退出码；影响业务结论的事实仍以 Result、Issue 或 Failure Diagnostic 为准。

- Research Artifact 保存七张版本化 Parquet 表；Raw 与 Research 不混用。
- Backtest Result 包含目标历史、每日净值、成交、未成交、指标、阶段指标和 Markdown 报告。
- Daily Target 保存策略目标；Daily Advice 另外保存账户事实约束下的建议 JSON/CSV。
- `result show --format json|markdown|summary` 只读取已发布事实，不重新计算。
- `backtest run`、`daily target` 和 `daily advise` 可显式添加 `--failure-report 路径`；失败时只发布独立诊断 Artifact，不伪造成功结果。
- 目标在决策日收盘后形成，只能从下一交易日起执行；回测按先卖后买和 T+1 可卖规则推进。
- Tushare、日线成交模型、费用默认值以及分红税模型都是研究假设，不代表真实成交或券商结算。

常见错误：`SECURITY_SECRET_MISSING` 表示当前进程未设置 Token；`DATA_PROVIDER_PERMISSION_DENIED` 表示当前积分没有接口权限；`ARTIFACT_OUTPUT_EXISTS` 要求换新路径或显式覆盖；`STRATEGY_WARMUP_INSUFFICIENT` 表示决策日前不足 313 个交易日；`ADVICE_*` Warning 表示建议按字段降级，并非伪成功成交。

## 6. 开发校验

Linux：

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
.venv/bin/python -m mypy src
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

需求上位文件 `PRD.txt` 与 `总体架构设计.md` 只读，不应由开发过程修改。
