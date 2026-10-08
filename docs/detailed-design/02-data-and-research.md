# 数据与研究层

## 1. Provider 与数据集

Provider 通过 HTTP API 获取 15 个 canonical dataset。注册表固定字段、业务键、参数、单位和空响应语义；
字段与业务键以 [registry.py](../../src/xqatexp/providers/tushare/registry.py) 为准。

| Dataset | 普通 API | Page size | 空响应 |
| --- | --- | ---: | --- |
| `dividend` | `dividend` | 100 | 允许 |
| `fina_indicator` | `fina_indicator` | 100 | 允许 |
| `fund_adj_factor` | `fund_adj` | 1000 | 拒绝 |
| `fund_basic` | `fund_basic` | 5000 | 拒绝 |
| `fund_daily` | `fund_daily` | 1000 | 拒绝 |
| `income` | `income` | 100 | 允许 |
| `index_daily` | `index_daily` | 1000 | 拒绝 |
| `stock_adj_factor` | `adj_factor` | 5000 | 拒绝 |
| `stock_basic` | `stock_basic` | 5000 | 拒绝 |
| `stock_daily` | `daily` | 6000 | 拒绝 |
| `stock_daily_basic` | `daily_basic` | 6000 | 拒绝 |
| `stock_price_limit` | `stk_limit` | 5800 | 拒绝 |
| `stock_st_status` | `stock_st` | 1000 | 允许 |
| `stock_suspend` | `suspend_d` | 1000 | 允许 |
| `trade_calendar` | `trade_cal` | 1000 | 拒绝 |

`income_vip` / `fina_indicator_vip` 分别映射到 income / fina_indicator，page size 均为 1000。
Raw 保留实际 API 名，ResearchBuilder 按 canonical dataset 分组。
stock_basic 拉取 SSE/SZSE 的 L/D/P/G 切片，fund_basic 拉取场内基金 L/D 切片。

ProviderConfig 独立于策略配置，默认 200/min、4 workers、5 attempts、30 秒 timeout；
5000 积分示例为 420/min。ProviderRuntime 管理共享 httpx.Client、连接池、全局及可选 API 限流器。
全局/API 许可同时取得，分页、探测和重试全部计入预算。取消事件可中断限流和退避等待。
429/业务限频进入共享 cooldown，5xx/网络/timeout 使用指数退避和 jitter；永久错误不重试。
错误按 authentication、permission、parameter、schema 和 temporary failure 分类，报告只保存脱敏元数据。

分页使用实际返回行数推进 offset，直到空终止页；重复行/页、字段变化或分页上限耗尽拒绝发布。
首次 VIP 调用以 limit=1 的相邻 offset 验证推进，无法确认时失败。

## 2. Raw Artifact

RawFetchService 发布一个显式 dataset/date scope 的 Raw Artifact：

- request.json：实际 API、字段、参数、时间、累计 attempts 和响应行数；
- response.jsonl.gz：provider 行及 _xqat_request_id、_xqat_page_number、_xqat_row_number；
- manifest.json：元数据、文件 SHA-256 和日期范围。

请求校验与路径检查先于联网。error 拒绝已有输出；skip 校验哈希、业务键、请求身份后跳过；
overwrite 使用 ArtifactPublisher 覆盖协议。响应的交易日、报告期和指定证券必须与请求匹配，
注册字段、行数及业务键在发布前检查。

JSON 使用 canonical encoding，gzip 固定 mtime。请求 UUID 和时间会变化，Raw 文件不保证跨次字节相同；
Research 业务 source_hash 排除 _xqat_ provenance 字段。
data check-raw 校验 Artifact 类型、Manifest、文件、行数、注册字段和业务键，不修改输入。

## 3. BatchRunner

BatchPlan 独立 TOML 描述范围、datasets、财务期、显式辅助请求及刷新窗口。
批次先取得真实 SSE 日历，为开市日生成日频分区，为季度末生成 VIP 财务分区。
ETF/指数显式请求也按交易日拆分；dividend 通配请求先取得 stock_basic 再展开逐股票任务。

线程池只提交 workers 数量的在途任务，单分区顺序分页。协调线程负责索引注册、跳过检查和报告更新。
bootstrap 校验并跳过已有成功分区，补抓失败/缺失/损坏；update 补洞、追加覆盖，默认刷新最近
5 个交易日、8 个报告期及快照。相同 plan/mode 的未完成 update 跳过已成功刷新任务。
权限/schema 错误阻止继续提交同 API，认证失败取消整个批次；已在途请求可完成。

每两秒和结束时原子保存检查点，索引先于报告写入。已发布但未索引的代次可恢复。
失败/中断报告保留任务状态与分类原因，Collection 完整标记为 false。

## 4. Raw Collection

Collection 是普通 Raw Artifact 的索引，目录结构和操作见 [数据操作指南](../data.md)。
partition id 是 canonical 请求身份的 SHA-256，身份包含 dataset/API、字段、参数和日期范围。
collection.json 保存 partition、generation、相对路径、Manifest hash、expected_partitions 和 complete。
每次抓取发布新代次；OS 文件锁约束单写入者，路径禁止逃逸和 symlink/junction。

读取必须完整、非空并验证选中代次及声明覆盖：

- 行情选择分区最新有效代次；
- stock_basic、fund_basic、每只证券 dividend 选择最新快照；
- 日历合并覆盖，同键采用新事实；
- 财务保留各代次不同披露版本，相同事实去重，同业务键不同内容报 DATA_CONFLICT；
- 非财务范围拒绝含混重叠；目录导入不能消除失败批次的覆盖声明。

Collection 可整体移动，读取不依赖绝对路径。索引和报告按独立 JSON Schema 校验，不是 Manifest Artifact。

## 5. Research Artifact

ResearchBuilder 把一个或多个 Raw Artifact 转换成固定七张 Parquet 表：

| 表 | 主键 | 主要用途 |
| --- | --- | --- |
| `security_master` | security_id | 证券类型、上市日、交易单位、price tick |
| `trade_calendar` | exchange + calendar_date | 开闭市、前后交易日 |
| `market_daily` | security_id + trade_date | raw OHLC、成交量/额、复权因子、research OHLC、available_from |
| `security_status_daily` | security_id + trade_date | 上市/ST/停牌/涨跌停锁定/risk flags |
| `financial_snapshot` | security_id + report_period + announce_date + revision_seq | point-in-time 财务事实 |
| `system_factor_daily` | factor_id + security_id + factor_date + factor_version | 版本化系统因子及输入窗口 |
| `corporate_action` | event_id | 分红、送股、拆分及日期 |

Research Artifact 的文件集合必须正好是上述七个 `tables/*.parquet` 加 Manifest。

## 6. 价格与单位

Raw 层保存 provider 值和单位来源。Research 层把成交量转换为 shares、金额转换为 CNY，并生成 raw 与 research 两套价格。

`research_open/high/low/close` 使用 adjustment factor 转换，供趋势和跨期研究；执行和真实成本相关逻辑使用 `open_raw/close_raw`。

## 7. 财务与系统因子

ResearchBuilder 按公告日和 available_from 选择当时可见的财务版本，派生季度利润、TTM 利润和年化 ROE。

系统因子由确定性算法计算，并记录 factor version、factor date、available_from、input start/end date 和 quality flags。ResearchCheck 明确检查 `input_end_date <= factor_date` 和 `available_from <= factor_date`。

## 8. ResearchSession

ResearchSession 是策略访问 Research Artifact 的唯一正规运行入口。

启动时：

1. 先执行 ResearchCheck；
2. 校验 Manifest 与七张表；
3. 创建 DuckDB in-memory connection；
4. memory limit 固定为 1GB；
5. threads 为 1–4；
6. 在输出父目录创建 session-owned 临时目录，要求至少 1GB 可用空间。

Session 关闭时删除自己的临时目录。

ResearchDataView 绑定 decision date 和 declaration lookback。历史查询拒绝越过决策日期、越过声明范围或读取尚未 available 的数据。

ResearchDataSlice.security_rules 按声明字段和 `SECURITY_RULES` 证券范围查询证券主表的
buy_lot_size、sell_lot_size 或 price_tick；仅返回 `rule_effective_from <= as_of_date` 的规则。
不依赖 security_status_daily；未知字段、未声明字段和范围外证券均被拒绝。

Execution 查询与研究查询分离：`execution_rows` 为指定执行日提供 raw 开高低收、估值 close、停牌/涨跌停、lot size 和 price tick；`corporate_actions` 提供账户事件日期；`benchmark_close` 固定读取沪深300指数 `000300.SH`。

## 9. Readiness

ReadinessChecker 优先按 StrategyDeclaration.data_requirements 评估。TRADING_DAYS 用实际天数计算 coverage；其他 requirement 通过 ResearchDataView 的 requirement coverage 查询实际数据覆盖。

required requirement 低于 minimum coverage 时，返回 declaration 指定的 failure code。Custom factor 在策略要求但未提供时 coverage 为 0。

SECURITY_RULES 对指定证券检查生效日期和声明规则字段的正值覆盖，分级止盈要求当前日
sell_lot_size 覆盖率 100%。

## 10. 自定义因子

用户自定义因子是 CSV，固定列：

`factor_name,security_id,factor_date,factor_value`

CustomFactorCheck 检查证券、日期、重复、数值与覆盖。当前只有 weekly allocation 策略支持可选 custom factor；启用后 ResearchSession 仍只负责系统 Research，CSV 由 `CsvCustomFactorView` 独立加载并按 decision date 限制可见性。

## 11. Build 与 Update

`data build` 接受完整 Collection 或重复 --raw-root，两者互斥，生成固定七张表。
`data update` 接受已验证 Research base 和显式 Raw roots，合并事实并重新计算状态/因子，发布完整 Research。
Collection 的 Raw 增量更新后仍从完整历史 build；不承诺局部 Research 计算或隐式 latest 发现。
指数主表来源记录按交易日/证券确定性选择，分区完成顺序不影响业务 source_hash。

构建配置只需 schema_version=1.0、start_date、end_date 和 strategy.csi300_etf_id。
策略运行的 readiness 与 Research 物理完整性分别校验；七张合法表允许包含空的非必需数据。
操作命令与范围设置见 [数据操作指南](../data.md)。
