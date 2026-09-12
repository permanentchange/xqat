# 15 详细设计 1.2 验证报告

**验证日期：** 2026-09-07
**验证结论：** 详细设计、共享合同、物理 Schema、实现、本地测试、干净环境安装和最小真实 Tushare 日线探测均已闭环且未发现相互冲突。`stock_daily` 当前权限与字段映射已由单次只读请求验证；不以 5100 积分推定其他接口权限。

> 历史证据说明：本报告记录 2026-09-07 当时的 Windows 11 x64 验证结果，不声明 Linux 仅为兼容性测试，也不替代当前 Linux 优先、Linux 与 Windows 均正式支持的工程基线（见 17）。

## 1. 上位输入保护

| 文件 | 开始与最终门禁 SHA-256 | 结论 |
|---|---|---|
| `PRD.txt` | `2CC90E04C56ADFCF4B9FA73354D3EF4D6DBE5B88FAE1B23D938D780D185D1148` | 未改变 |
| `总体架构设计.md` | `32D425F69DB9DB02D80079C622BD60B79E2C07780CC45C592A8A2A3C55DF4C15` | 未改变 |

该断言已固化为 `tests/contract/test_documentation.py`，后续修改上位输入会直接使测试失败。

## 2. 关键闭环结论

| 关键问题 | 唯一结论 | 权威位置 |
|---|---|---|
| 历史重放 | `ResearchDataView.slice(H)` 同时限制业务日期和 `available_from` | 01 §4；06 §2 |
| 理论策略回撤 | 已批准滚动 60 交易日峰值；320 日最大输入、252 日正式重放、最多 68 日预热 | 00 DD-004；07 §4、§10 |
| 账户与范围完整性 | COMPLETE/PARTIAL/UNKNOWN；空仓与未知严格分离；范围固定 STRATEGY_MANAGED | 01 §1、§6；11 §2–§6 |
| Artifact Schema | JSON/CSV/Parquet 的版本、字段、类型、空值、键、排序和兼容规则已冻结并有合同测试 | 05、06；16 |
| 自定义因子 | 同一内置策略提供默认关闭的正式评分槽，使用精确日期覆盖 | 06 §7；07 §3、§7 |
| Tushare 映射 | 必需接口、字段、分页、键、单位、空响应和权限要求已登记 | 04 §2–§6 |
| 成交与公司行为 | D/T、开盘成交、滑点、费用、T+1、分红税模型和送转拆分顺序已冻结 | 08；09；18 |
| 绩效 | 全期、自然年度和用户命名阶段共享同一公式；阶段可重叠但不回看选择 | 10 §2–§7；16 §9.2 |
| 工程与资源 | CPython 3.12、venv、锁文件、1GB DuckDB 上限、最多 4 线程、显式临时目录和可恢复发布 | 13；17 |

## 3. 可执行门禁证据

2026-09-07 在 Windows 11 x64、CPython 3.12.10 上执行：

| 门禁 | 实际结果 |
|---|---|
| Ruff format | 141 个文件已格式化，无差异 |
| Ruff lint | 通过 |
| mypy strict | 64 个生产源码文件通过 |
| pytest 离线全套 | 194 项收集；193 通过，1 个真实接口测试按 marker 排除，0 失败 |
| 总语句覆盖率 | 91.17%（门槛 90%） |
| 重点模块覆盖率 | ArtifactPublisher 92%；账户 98%；费用 98%；公司行为 98%；ResearchSession 94%；策略 99% |
| 文档合同与图表 | 上位哈希、本地链接/锚点、围栏、JPG 禁用、生产占位扫描通过；15 个 Mermaid 图经 Mermaid 11.17.2 + Edge 实际渲染为临时 SVG 全部成功 |
| 离线 CLI 链路 | Research 检查、Daily Target、Daily Advice、Backtest、Result Show 全部成功 |
| 全新 venv 安装 | 哈希锁依赖安装、wheel 构建/安装、help、自检、离线生成与回测全部成功 |

## 4. 一致性结论

- Strategy 只消费受限历史 Slice 和显式用户因子，不读取路径、时钟、账户或模拟成交状态。
- Backtest、Daily Target 与 Daily Advice 使用同一 Strategy Core；模拟账户与真实 AccountSnapshot 无共享持久状态。
- Target、实际模拟持仓和真实账户快照是三套独立事实；执行限制不改写 Target 历史。
- Raw、Research 和 Result 通过显式 Artifact 传递；没有 `latest` 自动发现或持久化 DuckDB 数据库。
- 结构化结果是报告的唯一事实源；重复运行测试证明非业务 UUID/时间不改变业务文件。
- `analysis_periods` 只在回测生效，结果固定为 `USER_DEFINED`；Daily 模式规范化为空。
- JSONL 运行日志只能由显式 `--log-file` 启用，且只记录脱敏阶段与退出码。

## 5. 外部验收结果

2026-09-07 通过当前用户专用的一次性内存管道向测试子进程注入 `TUSHARE_TOKEN`，显式执行 `pytest -m live_tushare --live-tushare -q`。测试仅调用一次 Tushare `daily` 接口（内部 dataset_id `stock_daily`，交易日 `20260904`），并验证登记字段全部存在、响应非空且所有记录交易日一致；结果为 1 通过、193 项未选中、0 失败，用时 2.28 秒。该证据只证明本次 `stock_daily` 权限，不推定其他接口权限；其余接口可按需用 `data capabilities` 逐项低调用量探测。测试结束后进程环境中的 Token 已清除，真实 Token 未写入任何项目文件、命令参数、测试、日志、报告或 Git 历史。

详细环境、命令、交付物和限制见 [最终验证报告](../final-verification-report.md)。
