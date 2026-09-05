# 17 Python 工程与运行基线

本文件是实现语言、依赖边界、包结构、开发工具、支持平台和统一验证命令的唯一权威定义。业务语义仍由 00–16 对应模块文档定义。

## 1. 语言、环境和平台

- 实现语言固定为 64 位 CPython 3.12，工程约束 `>=3.12,<3.13`；不使用其他语言实现业务逻辑。
- 环境管理只使用项目根目录 `.venv`，不使用 Conda，不读取用户全局 site-packages。
- 正式支持 Windows 10/11 x64；Linux x86_64 用于兼容性测试。路径和原子发布必须分别走平台适配器，不假设 POSIX 目录替换在 Windows 成立。
- 文本统一 UTF-8 无 BOM、LF；代码时区显式使用 `zoneinfo.ZoneInfo("Asia/Shanghai")`，正式 datetime 持久化为 UTC。
- 金额、价格、权重和费率使用 `decimal.Decimal`；因子、OLS 和波动使用 float64，并在领域边界检查有限值。禁止在两类数值之间隐式转换。

若系统没有兼容 Python，允许从 Python 官方发行渠道安装 CPython 3.12.x。安装只发生在文档门禁通过后，随后执行 `python -m venv .venv`。

## 2. 依赖边界

运行时直接依赖：

| 包 | 用途 | 边界 |
|---|---|---|
| `httpx` | 调用 Tushare HTTPS JSON API | 只在 `providers/tushare`。 |
| `duckdb` | 进程内查询显式 Parquet | 只在 `research`，不建持久数据库。 |
| `pyarrow` | Parquet/Arrow Schema 和流式读写 | 不向 Strategy 暴露路径。 |
| `numpy` | 确定性 float64 数值计算 | 不处理金额和费用。 |
| `jsonschema` | JSON 输入/输出合同校验 | Schema 由 16 生成/维护。 |

不使用 pandas 作为领域合同，不使用 Tushare SDK 的本地 Token 保存功能；客户端直接调用官方 HTTPS API，使原始字段和响应可忠实保存。不得增加 Web 框架、ORM、任务队列、持久数据库或交互式 UI。

开发依赖：`pytest`、`pytest-cov`、`hypothesis`、`ruff`、`mypy`、`pip-tools`。直接依赖在 `pyproject.toml` 声明兼容范围，并用 `pip-compile` 生成带哈希的 `requirements.lock` 和 `requirements-dev.lock`；代码和锁文件变更必须一起验证。

## 3. 包和文件结构

```text
XQAT/
  pyproject.toml
  requirements.lock
  requirements-dev.lock
  README.md
  src/xqatexp/
    __init__.py
    __main__.py
    cli.py
    config.py
    domain/
      enums.py
      contracts.py
      issues.py
      numeric.py
    artifacts/
      schemas.py
      manifest.py
      publisher.py
      readers.py
    providers/tushare/
      client.py
      registry.py
      capability.py
      raw.py
    research/
      preparation.py
      tables.py
      factors.py
      session.py
      readiness.py
      custom_factors.py
    strategy/
      declaration.py
      scoring.py
      replay.py
      market_regime.py
      drawdown.py
      weekly_strategy.py
    portfolio/
      validation.py
      transitions.py
      rebalance.py
    backtest/
      engine.py
      execution.py
      fees.py
      corporate_actions.py
      account.py
    performance/
      metrics.py
      periods.py
      contribution.py
    daily/
      account_snapshot.py
      target.py
      advice.py
    reporting/
      structured.py
      markdown.py
    application/
      services.py
  schemas/
  examples/
  tests/
    unit/
    contract/
    integration/
    e2e/
    golden/
    fixtures/
```

每个文件只承担表中名称表达的单一职责。跨包依赖方向固定为：`domain` ← `artifacts/providers/research/strategy/portfolio/backtest/performance/daily` ← `application` ← `cli`。Strategy 只能依赖 domain Protocol 和自身纯计算模块。

## 4. CLI 和异常边界

发行包同时提供 `xqatexp` console script 和 `python -m xqatexp`，二者调用同一个 `main(argv: Sequence[str] | None) -> int`。CLI 只捕获应用边界异常并映射 02 的退出码；领域模块返回值或抛出已定义异常，不调用 `sys.exit`、不打印、不读环境变量。

`TUSHARE_TOKEN` 只由 composition root 读取并包装为不会 `repr` 明文的 `SecretValue`，只传给 TushareClient。测试必须捕获 stdout、stderr、日志、异常、Manifest 和临时文件验证完整 Token 及长度不少于 8 的片段均不存在。

## 5. 代码质量门禁

- Ruff format 和 lint 必须通过；配置固定在 `pyproject.toml`。
- mypy 对 `src/xqatexp` 使用 strict 基线；第三方无类型包的忽略必须逐包显式记录，禁止全局 `ignore_missing_imports=true`。
- pytest 测试名称表达业务事实，不以实现私有方法为主要断言。
- 总体语句覆盖率不低于 90%；Strategy、账户守恒、费用、公司行为、ArtifactPublisher 和历史可见性模块不低于 95%。覆盖率不是验收替代物，仍必须通过 14 的合同、属性、黄金和端到端测试。
- 随机属性测试固定报告 seed；失败用例进入回归夹具。
- 正式代码禁止未完成占位、未实现异常、空函数和仅为测试存在的生产分支。

## 6. 资源默认值

| 配置 | 默认 | 约束 |
|---|---:|---|
| `duckdb_memory_limit` | `1GB` | 可配置，最低 256MB。 |
| `duckdb_threads` | `min(4, logical_cpu_count)` | 至少 1，结果必须与线程数无关。 |
| `http_timeout_seconds` | 30 | 5–120。 |
| `provider_max_attempts` | 5 | 1–8。 |
| `provider_requests_per_minute` | 200 | 不超过能力探测/用户配置允许值。 |
| `parquet_compression` | `zstd` | MVP 固定。 |

DuckDB 临时目录位于用户显式工作目录或输出父目录下的 `.xqatexp-tmp/<run_id>`，不能位于系统未知共享目录。运行前要求可用空间至少为预计新输出的 2 倍；估计无法取得时只做可用空间绝对下限 1GB 检查并写 limitation，不缩小业务范围。

## 7. Windows 原子发布算法

新目标发布：在目标同一父目录创建唯一 staging，完整写入、关闭句柄、校验后用单次同卷目录 rename 到不存在的正式路径；rename 失败则保留旧状态并清理 staging。

显式 OVERWRITE 在 Windows 不声称一次目录替换具有 POSIX 原子性，采用可恢复交换协议：

1. 完整构建并校验 `.<target>.staging-<run_id>`；
2. 创建只含目标、旧目录、新目录和阶段的 `.<target>.swap-<run_id>.json`，刷新文件和父目录可用元数据；
3. 将旧正式目录 rename 为 `.<target>.backup-<run_id>`；
4. 将 staging rename 为正式目录；
5. 重新读取正式 Manifest 和所有摘要；成功后删除交换记录，再删除 backup；
6. 任一步失败：若正式目录缺失且 backup 完整，立即恢复 backup；若正式与 backup 同时存在，以正式 Manifest 完整性决定保留正式或回滚；永不删除最后一个已验证完整版本。

每次启动和每次发布前扫描目标同父目录中与该目标精确匹配的 swap 记录并执行相同恢复状态机。路径必须先解析并验证仍位于用户明确目标父目录，清理操作不得使用宽泛 glob。

POSIX 可在平台适配器中使用受支持的原子 rename；两种实现共享相同恢复后置条件：正式路径不存在或指向一个摘要全部通过的完整 Artifact。

## 8. 统一验证命令

从已激活 `.venv` 的项目根目录执行：

```text
python -m pip install --require-hashes -r requirements-dev.lock
python -m ruff format --check .
python -m ruff check .
python -m mypy src/xqatexp
python -m pytest -m "not live_tushare" --cov=xqatexp --cov-report=term-missing --cov-fail-under=90
python -m xqatexp --help
python -m xqatexp self-check --offline
```

真实集成使用单独标记和显式开关：

```text
python -m pytest -m live_tushare --run-live-tushare -q
```

未提供 Token 时该命令必须以明确的 `SECURITY_SECRET_MISSING` 失败，不伪装为通过或普通跳过。普通离线测试不得访问网络。

## 9. 安装与交付验证

最终验收必须在删除并重新创建的干净 `.venv` 中按 README 安装锁文件和项目，然后运行统一门禁、离线完整示例和最小真实 Tushare 链路。删除 `.venv` 前必须验证解析后的绝对路径就是项目根下的 `.venv`；不得删除用户其他环境。
