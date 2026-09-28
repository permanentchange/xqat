# XQatExp

XQatExp 是一个本地运行的纯 Python A 股量化研究工具。它将显式的 Tushare 原始数据构建为不可变 Research Artifact，并通过 Strategy Registry 支持 allocation 型周频策略与 execution-state 驱动的 stateful 策略。现有能力包括历史回测、每日目标、stateful 每日决策，以及账户事实约束下的参考建议。

## 1. 安装（Linux，首选）

正式支持 Linux x86_64 和 Windows 10/11 x64；Linux 优先。Linux 验收基线是 Ubuntu 22.04.5 LTS（WSL2、glibc 2.35），最低 Linux ABI 为 glibc 2.28 级别。运行时使用 Conda 创建独立的 Python 3.12 环境。在仓库根目录执行：

```bash
conda create -n xqat python=3.12 -y
conda activate xqat
python -m pip install --upgrade pip
python -m pip install --require-hashes -r requirements.lock
python -m pip install --require-hashes -r requirements-build.lock
python -m pip install --no-deps --no-build-isolation .
xqatexp self-check --offline
```

`requirements-build.lock` 只锁定构建后端。上面的步骤足够运行程序和执行离线示例；如果需要运行 pytest、Hypothesis property tests、Ruff 或 Mypy，再安装完整开发依赖：

```bash
python -m pip install --require-hashes -r requirements-dev.lock
```

`requirements-dev.lock` 已包含 `pytest`、`pytest-cov`、`hypothesis`、`ruff`、`mypy` 等测试与开发工具，不需要再单独 `pip install pytest` 或 `pip install hypothesis`。需要源码可编辑安装的开发者可把项目安装命令改为 `python -m pip install --no-deps --no-build-isolation -e .`。

## 2. 安装（Windows PowerShell）

Windows 10/11 x64 同样正式支持，使用独立的 Conda Python 3.12 环境。在仓库根目录执行：

```powershell
conda create -n xqat python=3.12 -y
conda activate xqat
python -m pip install --upgrade pip
python -m pip install --require-hashes -r requirements.lock
python -m pip install --require-hashes -r requirements-build.lock
python -m pip install --no-deps --no-build-isolation .
xqatexp self-check --offline
```

如果要在 Windows 上运行测试或开发检查，同样先执行：

```powershell
python -m pip install --require-hashes -r requirements-dev.lock
```

Linux 与 Windows 应分别创建自己的 Conda 环境，不共享同一个环境目录。若同一工作目录可从 Windows 与 WSL 的 `/mnt/c` 访问，正式 Linux 验收仍建议在 WSL 原生 ext4 文件系统中的干净仓库副本中运行，避免 DrvFS 掩盖 POSIX 权限、rename 或符号链接问题。每次进入仓库开始工作前先执行 `conda activate xqat`。

## 3. 完整离线示例

下面所有命令都假设已经执行 `conda activate xqat`。离线示例使用确定性的合成行情，不访问网络，也不需要 Token。Linux：

```bash
python examples/generate_offline_example.py --root .example-work
xqatexp data check-research --input .example-work/research --report .example-work/research-check.json
xqatexp daily target --config examples/config-offline.toml --decision-date 2026-09-04 --output .example-work/daily-target
xqatexp daily advise --config examples/config-offline.toml --target .example-work/daily-target --account examples/account-complete.json --output .example-work/daily-advice
xqatexp backtest run --config examples/config-offline.toml --start-date 2026-08-31 --end-date 2026-09-07 --output .example-work/backtest
xqatexp result show --input .example-work/backtest --format markdown
```

Windows PowerShell 对应命令：

```powershell
python examples\generate_offline_example.py --root .example-work
xqatexp data check-research --input .example-work\research --report .example-work\research-check.json
xqatexp daily target --config examples\config-offline.toml --decision-date 2026-09-04 --output .example-work\daily-target
xqatexp daily advise --config examples\config-offline.toml --target .example-work\daily-target --account examples\account-complete.json --output .example-work\daily-advice
xqatexp backtest run --config examples\config-offline.toml --start-date 2026-08-31 --end-date 2026-09-07 --output .example-work\backtest
xqatexp result show --input .example-work\backtest --format markdown
```

重复生成示例时给生成脚本加 `--overwrite`；重复写正式结果时，只有明确指定 `--existing overwrite` 才会替换已验证结果。

回测配置可在顶层加入 `analysis_periods = [{ label = "阶段名", start_date = "YYYY-MM-DD", end_date = "YYYY-MM-DD" }]`。阶段可重叠但必须位于回测范围内，结果写入 `period_metrics.csv`，并明确标记为 `USER_DEFINED`。

自定义因子路径，Linux：

```bash
xqatexp factor check --file .example-work/custom-factor.csv --research .example-work/research --strategy weekly_market_guard_rank_v1 --start 2026-08-31 --end 2026-09-04 --report .example-work/factor-check.json
xqatexp daily target --config examples/config-custom-factor.toml --decision-date 2026-09-04 --custom-factor .example-work/custom-factor.csv --output .example-work/custom-daily-target
```

Windows PowerShell 对应命令：

```powershell
xqatexp factor check --file .example-work\custom-factor.csv --research .example-work\research --strategy weekly_market_guard_rank_v1 --start 2026-08-31 --end 2026-09-04 --report .example-work\factor-check.json
xqatexp daily target --config examples\config-custom-factor.toml --decision-date 2026-09-04 --custom-factor .example-work\custom-factor.csv --output .example-work\custom-daily-target
```

`account-complete.json`、`account-empty.json`、`account-partial.json` 和 `account-unknown.json` 展示不同账户完整性。账户缺失、部分或未知时，目标权重仍保留，但系统不会臆造当前持仓或可执行数量。建议固定包含“仅供研究参考”的非订单声明。

### Stateful staged drawdown 示例

`staged_drawdown_v1` 是 execution-state 驱动策略。首次运行先创建显式状态，Daily 决策不会从历史建议或账户快照猜测成交事实。Linux：

```bash
xqatexp state init --strategy-id staged_drawdown_v1 --strategy-version 1.0.0 --initial-capital 1000000 --output .example-work/staged-state.json
xqatexp daily decide --config examples/config-staged-drawdown.toml --decision-date 2026-09-04 --state .example-work/staged-state.json --output .example-work/staged-decision
```

Windows PowerShell：

```powershell
xqatexp state init --strategy-id staged_drawdown_v1 --strategy-version 1.0.0 --initial-capital 1000000 --output .example-work\staged-state.json
xqatexp daily decide --config examples\config-staged-drawdown.toml --decision-date 2026-09-04 --state .example-work\staged-state.json --output .example-work\staged-decision
```

`daily decide` 输出的是下一交易日开盘执行意图，而不是成交确认。真实成交后必须用实际成交事实推进状态，例如 Linux：

```bash
xqatexp state apply-fill \
  --input .example-work/staged-state.json \
  --execution-date 2026-09-07 \
  --security-id 600000.SH \
  --side BUY \
  --quantity 10000 \
  --execution-price 9.80 \
  --commission 5 \
  --transfer-fee 0 \
  --stamp-duty 0 \
  --output .example-work/staged-state-next.json
```

下一次 `daily decide` 应把 `staged-state-next.json` 作为 `--state`。如果同时提供 `--account`，系统只做明确的一致性校验；不会用账户快照自动修补 last buy price、成本基础或历史成交。

如果发生已确认的送股或拆分，也不要手工改 state；用 `state apply-stock-adjustment` 写入实际新增股份，例如：

```bash
xqatexp state apply-stock-adjustment \
  --input .example-work/staged-state-next.json \
  --effective-date 2026-09-08 \
  --event-id distribution-20260908 \
  --security-id 600000.SH \
  --kind STOCK_DISTRIBUTION \
  --added-quantity 1000 \
  --output .example-work/staged-state-after-action.json
```

该操作保持剩余成本基础不变，并按持股扩张比例调整 `last_buy_price` 锚点。

当前日线数据口径下，信号在 D 日收盘后生成，并从 D+1 开盘执行。20 日趋势使用复权后的 `research_close`；与“上次实际买入价”的加仓比较和整体持仓收益率使用 `close_raw`，与实际成交价保持同一价格口径。默认“缓慢下跌”定义为：20 个收盘观察内累计跌幅至少 10%、任一单日跌幅不超过 5%、19 个日收益中至少 12 天下跌；这些阈值都可在 `[strategy]` 中修改。

止盈优先于继续加仓。剩余持仓成本基础包含买入费用，卖出后按持股比例释放成本；累计买入金额用于约束单轮持仓周期内的最大初始资金投入，完全清仓后下一次首次买入会开始新的累计周期。

## 4. Tushare 数据

本节中的 live pytest 需要已经安装 `requirements-dev.lock`。Token 仅从当前进程的 `TUSHARE_TOKEN` 环境变量读取，不允许写入 TOML、JSON、源码或命令参数。请在本机临时设置自己的值。Linux：

```bash
export TUSHARE_TOKEN="<在本机填写你的 Token>"
xqatexp data capabilities --output .local/tushare-capabilities.json
python -m pytest -m live_tushare --live-tushare -q
xqatexp data fetch --dataset stock_daily --start 2026-08-01 --end 2026-08-31 --output data/raw/stock-daily-202608
```

Windows PowerShell 的进程局部设置和对应命令：

```powershell
$env:TUSHARE_TOKEN = "<在本机填写你的 Token>"
xqatexp data capabilities --output .local\tushare-capabilities.json
python -m pytest -m live_tushare --live-tushare -q
xqatexp data fetch --dataset stock_daily --start 2026-08-01 --end 2026-08-31 --output data\raw\stock-daily-202608
```

最小真实接口测试需要显式开关，且只发起一次日线请求。能力探测会逐项报告当前积分可调用的接口。正式抓取每次只处理一个显式数据集和日期范围；随后对每个 Raw Artifact 执行 `data check-raw`，并把构建所需的各个 `--raw-root` 显式传给 `data build`。工具不会自动寻找 `latest`，也不会在回测、每日模式或结果查看时访问网络。

## 5. 产物与解释

需要保留最小运行事件时，把全局参数放在业务命令之前，例如 `xqatexp --log-file .local/run.jsonl backtest run ...`。日志为脱敏 JSON Lines，只记录命令阶段和退出码；影响业务结论的事实仍以 Result、Issue 或 Failure Diagnostic 为准。

- Research Artifact 保存七张版本化 Parquet 表；Raw 与 Research 不混用。
- Backtest Result 包含目标历史、每日净值、成交、未成交、指标、阶段指标和 Markdown 报告。
- Daily Target 保存 allocation 策略目标；Daily Decision 保存 stateful 策略的 trade intents、显式 strategy state 与 diagnostics；Daily Advice 另外保存账户事实约束下的建议 JSON/CSV。
- `result show --format json|markdown|summary` 只读取已发布事实，不重新计算。
- `backtest run`、`daily target`、`daily decide` 和 `daily advise` 可显式添加 `--failure-report 路径`；失败时只发布独立诊断 Artifact，不伪造成功结果。
- 目标在决策日收盘后形成，只能从下一交易日起执行；回测按先卖后买和 T+1 可卖规则推进。
- Tushare、日线成交模型、费用默认值以及分红税模型都是研究假设，不代表真实成交或券商结算。

常见错误：`SECURITY_SECRET_MISSING` 表示当前进程未设置 Token；`DATA_PROVIDER_PERMISSION_DENIED` 表示当前积分没有接口权限；`ARTIFACT_OUTPUT_EXISTS` 要求换新路径或显式覆盖；`STRATEGY_WARMUP_INSUFFICIENT` 表示决策日前不足 313 个交易日；`ADVICE_*` Warning 表示建议按字段降级，并非伪成功成交。

## 6. 开发校验

先确认开发依赖已安装：

```bash
python -m pip install --require-hashes -r requirements-dev.lock
```

然后执行。Linux：

```bash
python -m pytest -q
python -m ruff check src tests
python -m mypy src
```

Windows PowerShell：

```powershell
python -m pytest -q
python -m ruff check src tests
python -m mypy src
```

需求上位文件 `PRD.txt` 与 `总体架构设计.md` 只读，不应由开发过程修改。
