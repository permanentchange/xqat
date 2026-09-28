# XQatExp

XQatExp 是一个本地运行的 A 股量化研究与决策工具。项目以版本化 Artifact 为运行边界，支持 Tushare Raw 数据准备、统一 Research Artifact、allocation 策略、stateful TradeIntent 策略、历史回测、每日目标/决策和账户事实约束下的研究建议。

- 总体架构：[docs/architecture.md](docs/architecture.md)
- 详细设计：[docs/detailed-design/README.md](docs/detailed-design/README.md)
- 文档索引：[docs/README.md](docs/README.md)

## 1. 安装（Linux，首选）

正式支持 Linux x86_64 和 Windows 10/11 x64；Linux 优先。Linux 验收基线是 Ubuntu 22.04.5 LTS，最低 Linux ABI 兼容目标为 glibc 2.28。运行时使用 Conda 创建独立 Python 3.12 环境：

```bash
conda create -n xqat python=3.12 -y
conda activate xqat
python -m pip install --upgrade pip
python -m pip install --require-hashes -r requirements.lock
python -m pip install --require-hashes -r requirements-build.lock
python -m pip install --no-deps --no-build-isolation .
xqatexp self-check --offline
```

`requirements-build.lock` 锁定构建后端。需要运行 pytest、Hypothesis、Ruff 或 Mypy 时安装：

```bash
python -m pip install --require-hashes -r requirements-dev.lock
```

需要 editable install 时使用：

```bash
python -m pip install --no-deps --no-build-isolation -e .
```

## 2. 安装（Windows PowerShell）

Windows 10/11 x64 使用独立 Conda Python 3.12 环境：

```powershell
conda create -n xqat python=3.12 -y
conda activate xqat
python -m pip install --upgrade pip
python -m pip install --require-hashes -r requirements.lock
python -m pip install --require-hashes -r requirements-build.lock
python -m pip install --no-deps --no-build-isolation .
xqatexp self-check --offline
```

开发/测试依赖：

```powershell
python -m pip install --require-hashes -r requirements-dev.lock
```

Linux 与 Windows 应分别创建自己的 Conda 环境，不共享同一个环境目录。WSL 正式 Linux 验证建议使用 WSL 原生 ext4 工作区。

## 3. 当前内置策略

### weekly_market_guard_rank_v1

周频 allocation 策略。每周最后交易日收盘决策，输出通用 TargetPortfolio。策略使用 A 股系统因子、沪深300ETF regime、持仓 rank hysteresis 和理论组合回撤 overlay；可选接入单个用户 custom factor。详细逻辑、参数和运行方式见 [策略 README](src/xqatexp/strategy/strategies/weekly_market_guard_rank_v1/README.md)。

### staged_drawdown_v1

日频 confirmed-state 策略。默认规则：

- 20 个收盘观察内累计跌幅至少 10%；
- 最差单日跌幅不超过 5%；
- 至少 12 个下跌日；
- 首次和追加买入每档为初始资金 10%；
- 相比上次实际 BUY 成交价再跌 10% 才追加；
- 当前持仓周期累计实际买入 gross notional 上限为初始资金 100%；
- 整体持仓收益率达到 10% 时，卖出当前持仓 20%。

趋势使用复权后的 `research_close`；加仓锚点和持仓收益使用 `close_raw`。决策在 D 日收盘后产生，当前执行模型为 D+1 NEXT_OPEN，不模拟 9:15–9:25 集合竞价订单簿。详细逻辑、参数和运行方式见 [策略 README](src/xqatexp/strategy/strategies/staged_drawdown_v1/README.md)。

### 策略源码组织

共享策略框架位于 `src/xqatexp/strategy/`，只包含 Registry、Decision/Intent、Schedule 和 Strategy State 等通用机制。具体策略按 strategy id 放在独立目录中：

```text
src/xqatexp/strategy/
├── registry.py
├── decision.py
├── intents.py
├── schedule.py
├── state.py
├── state_io.py
├── state_tools.py
└── strategies/
    ├── weekly_market_guard_rank_v1/
    │   ├── parameters.py
    │   ├── declaration.py
    │   ├── strategy.py
    │   ├── scoring.py
    │   ├── market_regime.py
    │   └── drawdown.py
    └── staged_drawdown_v1/
        ├── parameters.py
        ├── declaration.py
        └── strategy.py
```

新增策略应建立 `strategies/<strategy_id>/`，提供参数标准化、StrategyDeclaration、策略实现，并在全局 `strategy/registry.py` 注册。普通新策略不应通过修改 BacktestEngine、ResearchSession、Account 或 Reporting 来接入。

## 4. 完整离线示例

以下命令假设已经 `conda activate xqat`：

```bash
python examples/generate_offline_example.py --root .example-work
xqatexp data check-research --input .example-work/research --report .example-work/research-check.json
xqatexp daily target --config examples/config-offline.toml --decision-date 2026-09-04 --output .example-work/daily-target
xqatexp daily advise --config examples/config-offline.toml --target .example-work/daily-target --account examples/account-complete.json --output .example-work/daily-advice
xqatexp backtest run --config examples/config-offline.toml --start-date 2026-08-31 --end-date 2026-09-07 --output .example-work/backtest
xqatexp result show --input .example-work/backtest --format markdown
```

Windows PowerShell 使用相同命令语义，仅把路径分隔符换成 `\`。

## 5. Stateful Daily 工作流

初始化 state：

```bash
xqatexp state init --strategy-id staged_drawdown_v1 --strategy-version 1.0.0 --initial-capital 1000000 --output .local/staged-state.json
```

生成下一交易日 intent：

```bash
xqatexp daily decide --config examples/config-staged-drawdown.toml --decision-date 2026-09-04 --state .local/staged-state.json --output .local/staged-decision
```

真实成交后，只用 confirmed fill 更新 state：

```bash
xqatexp state apply-fill --input .local/staged-state.json --execution-date 2026-09-07 --security-id 600000.SH --side BUY --quantity 10000 --execution-price 9.80 --commission 5 --output .local/staged-state-next.json
```

已确认送股/拆分使用：

```bash
xqatexp state apply-stock-adjustment --input .local/staged-state-next.json --effective-date 2026-09-08 --event-id distribution-20260908 --security-id 600000.SH --kind STOCK_DISTRIBUTION --added-quantity 1000 --output .local/staged-state-after-action.json
```

Daily Decision 不从历史 advice、target 或 AccountSnapshot 推测成交。可选 `--account` 只做数量一致性校验。

## 6. Tushare 数据

Token 只从当前进程的 `TUSHARE_TOKEN` 环境变量读取，不允许写入配置、源码或命令参数。

Linux：

```bash
export TUSHARE_TOKEN="<在本机填写你的 Token>"
xqatexp data capabilities --output .local/tushare-capabilities.json
xqatexp data fetch --dataset stock_daily --start 2026-08-01 --end 2026-08-31 --output data/raw/stock-daily-202608
xqatexp data check-raw --input data/raw/stock-daily-202608 --report .local/raw-check.json
```

Research 数据通过显式 Raw roots 构建或更新：

```bash
xqatexp data build --raw-root data/raw/stock-daily-202608 --config examples/config-offline.toml --output data/research/example
xqatexp data check-research --input data/research/example --report .local/research-check.json
```

实际 Research 构建通常需要策略所需的多类 Raw Artifact，而不是仅一份 stock_daily。

## 7. 自定义因子

Custom factor CSV 固定列为 `factor_name,security_id,factor_date,factor_value`。先验证再用于 weekly 策略：

```bash
xqatexp factor check --file .example-work/custom-factor.csv --research .example-work/research --strategy weekly_market_guard_rank_v1 --start 2026-08-31 --end 2026-09-04 --report .local/factor-check.json
```

## 8. 结果与账户建议

- `daily target`：allocation 目标权重；
- `daily decide`：stateful trade intents；
- `daily advise`：TargetPortfolio + 可选 AccountSnapshot 推导的研究参考建议；
- `result show`：只读取已发布 Artifact，不重新计算。

TradeAdvice 不是订单。账户事实缺失或不完整时，系统保留 unresolved/limitations，不臆造持仓、现金或可卖数量。

## 9. 开发校验

```bash
python -m pytest -m "not live_tushare" -q
python -m ruff check src tests
python -m mypy src
python -m xqatexp self-check --offline
```

Live Tushare 测试必须显式提供 Token：

```bash
export TUSHARE_TOKEN="<在本机填写你的 Token>"
python -m pytest -m live_tushare --live-tushare -q
```

CI 以 Linux 为主门禁，Windows 为兼容性 job；Ruff/Mypy 为 advisory，功能测试、CLI help 和 offline self-check 为 Linux 阻断项。

## 10. 公共命令

`self-check`、`data capabilities`、`data fetch`、`data check-raw`、`data build`、`data update`、`data check-research`、`factor check`、`backtest run`、`daily target`、`daily decide`、`daily advise`、`state init`、`state apply-fill`、`state apply-stock-adjustment`、`result show`。
