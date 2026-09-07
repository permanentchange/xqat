# XQatExp MVP 最终验证报告

## 1. 当前结论

截至 2026-09-07，详细设计、纯 Python 实现、Schema、自动化测试、离线端到端流程、全新 venv 安装验收和用户文档均已完成。本地交付满足开发与离线使用条件。唯一尚未执行的外部验收是使用当前进程环境中的 `TUSHARE_TOKEN` 发起最小真实 Tushare 请求；当前环境变量未注入，因此本报告不声称任何真实接口权限可用。

## 2. 环境

| 项目 | 实际值 |
|---|---|
| 操作系统 | Microsoft Windows 11 专业版 10.0.26200 x64 |
| Python | CPython 3.12.10 64-bit |
| 环境管理 | 项目根目录 `.venv`（标准库 venv） |
| Git | 2.54.0.windows.1 |
| 核心运行依赖 | duckdb 1.5.5；httpx 0.28.1；jsonschema 4.26.0；numpy 2.5.2；pyarrow 23.0.1 |
| 锁定方式 | 运行、构建与开发分别使用 `requirements.lock`、`requirements-build.lock`、`requirements-dev.lock`，均含完整哈希 |

## 3. 交付范围

- 20 份 `docs/detailed-design/` Markdown 详细设计文件，含 15 个 Mermaid 时序图、数据流图、状态图或模块关系图；无 JPG/JPEG 依赖。
- 64 个生产 Python 文件，覆盖配置、领域合同、Artifact、Tushare、Research、策略、组合、回测、绩效、每日建议、报告、日志和应用编排。
- 9 个版本化 JSON Schema，以及代码中的 CSV/Arrow 物理 Schema 和主键、排序、Major 版本校验。
- 51 个自动化测试文件，覆盖单元、属性、合同、集成、黄金、端到端、错误路径和显式真实接口冒烟层次。
- 不含真实凭据的离线配置、自定义因子生成器和四类 AccountSnapshot 示例。
- 根目录 README 提供 Windows PowerShell/Git Bash 安装、数据、回测、每日运行、日志、结果查看和常见错误命令。

## 4. 实际执行的质量门禁

```powershell
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy src/xqatexp
.\.venv\Scripts\python.exe -m pytest -m "not live_tushare" --cov=xqatexp --cov-report=term-missing --cov-fail-under=90
```

结果：

| 检查 | 结果 |
|---|---|
| 格式 | 141 个文件已格式化 |
| Lint | 通过 |
| mypy strict | 64 个生产源码文件通过 |
| 测试 | 194 项收集；193 通过，1 个真实接口测试按 marker 排除，0 失败 |
| 总覆盖率 | 91.17% |
| ArtifactPublisher | 92% |
| 模拟账户 | 98% |
| 费用 | 98% |
| 公司行为 | 98% |
| ResearchSession | 94% |
| 内置策略 | 99% |

唯一排除项为 `live_tushare`，原因是当前进程没有 `TUSHARE_TOKEN`；普通测试没有访问网络或消耗 Tushare 积分。显式运行真实测试但仍缺少变量时会以 `SECURITY_SECRET_MISSING` 失败。

## 5. 离线端到端验收

按 README 的用户顺序实际完成：

1. 生成确定性 Research Artifact 和用户因子；
2. `data check-research` 返回 `valid=true`；
3. 生成 Daily Target；
4. 使用完整账户示例生成 Daily Advice；
5. 运行 Backtest；
6. 用 `result show` 读取已发布报告；
7. 检查 `period_metrics.csv` 同时包含 `FULL`、`CALENDAR_YEAR` 和 `USER_DEFINED/example-week`。

离线样例回测区间为 2026-08-31 至 2026-09-07，共 6 个估值点、5 个收益区间；该短区间只验证完整链路，不代表策略收益评价。

## 6. 全新安装验收

在新建 `.venv-acceptance` 中实际完成：

1. `pip install --require-hashes -r requirements.lock`；
2. 从当前源码构建 `xqatexp-0.1.0-py3-none-any.whl`；
3. 使用 `--no-deps --no-index` 安装该 wheel；
4. 运行安装后的 `xqatexp --help`；
5. 运行 `xqatexp self-check --offline`；
6. 使用安装包生成独立离线 Research；
7. 运行安装后的 Backtest 与 Result Show。

最终返回 `INSTALL_ACCEPTANCE_OK`。验收临时环境、wheel 和运行结果在记录证据后清理，不作为产品输入提交。

## 7. 文档、Schema 与完整性检查

- 自动合同测试逐项核对 PRD 和总体架构 SHA-256、Markdown 本地链接及标题锚点、代码围栏、Mermaid 声明、JPG/JPEG 禁用和生产代码占位词；另用 Mermaid 11.17.2 与本机 Edge 将 15 个图实际渲染为临时 SVG，全部成功，临时文件随后清理。
- PRD 27.1–27.8 的 61 个验收条目在 [测试与需求追踪](detailed-design/14-testing-and-traceability.md) 中逐项映射到设计位置和验证项。
- JSON、CSV 和 Parquet 的生产者/消费者合同测试全部通过；重复回测的业务文件逐字节一致。
- 64 位十六进制敏感信息扫描对依赖锁中的合法包摘要、两份上位输入公开 SHA-256 和测试中的单字符重复摘要分别做白名单处理；其余源码、文档、示例、测试和配置中没有未识别的疑似 Token 值。
- 上位文件最终摘要与开始值一致：

| 文件 | SHA-256 |
|---|---|
| `PRD.txt` | `2CC90E04C56ADFCF4B9FA73354D3EF4D6DBE5B88FAE1B23D938D780D185D1148` |
| `总体架构设计.md` | `32D425F69DB9DB02D80079C622BD60B79E2C07780CC45C592A8A2A3C55DF4C15` |

## 8. 真实 Tushare 验收状态

当前状态：**未执行，等待当前进程注入环境变量。**

程序仅从 `TUSHARE_TOKEN` 读取凭据。最小冒烟测试固定为一次 `daily` 请求，并校验返回字段、非空数据和请求交易日；能力探测逐数据集低调用量执行，配置了每分钟 200 次的进程内令牌桶。5100 积分只作为用户说明记录，不作为任何接口权限证明。

注入后执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -m live_tushare --live-tushare -q
.\.venv\Scripts\xqatexp.exe data capabilities --output .local\tushare-capabilities.json
```

第二条会探测全部登记接口，调用量高于单请求冒烟；先运行第一条即可完成最小映射验收。任何权限不足会以机器可读错误返回，不使用伪数据或静默替代。

## 9. 已知边界与研究假设

- 工具不连接券商、不自动同步账户、不下单，也不维护真实成交历史；建议仅供研究参考。
- 成交使用日线下一交易日开盘、显式滑点、容量、交易单位和费用模型，不能复现真实盘口路径。
- 默认现金分红使用供应商税后字段并披露持有期个税差异；配股等不支持事件在持仓相关时明确失败。
- 仅支持内置周频策略、A 股个股、指定沪深300ETF和现金，不提供动态策略市场或参数自动寻优。
- 用户因子由用户保证计算逻辑和历史可见性；系统检查格式、证券、日期和值及策略所需覆盖。
- 短样本下年化或滚动指标会产生明确 limitation，而不是输出伪精度。

## 10. 最终判定条件

本地代码与文档验收已经通过。当前 Goal 只有在当前进程注入 Token，并完成最小真实 Tushare 冒烟测试且记录其成功或准确权限诊断后，才可标记为完全完成。
