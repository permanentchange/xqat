# 02 数据与研究层详细设计

## 1. Tushare 数据集注册表

Provider 注册表当前支持 15 个 dataset id：

`stock_basic`、`trade_calendar`、`stock_daily`、`stock_adj_factor`、`stock_daily_basic`、`stock_suspend`、`stock_price_limit`、`stock_st_status`、`fund_basic`、`fund_daily`、`fund_adj_factor`、`index_daily`、`income`、`fina_indicator`、`dividend`。

每个 DatasetSpec 固定 api name、字段、业务主键、可选固定参数、单位和积分要求。Raw fetch 不允许静默省略注册字段。

## 2. Raw Artifact

每次 `data fetch` 只发布一个显式 dataset/date range 的不可变 Raw Artifact：

- `request.json`：请求 id、provider、API、字段、参数、页码、时间、attempt count、row count；
- `response.jsonl.gz`：原始 provider 行，加 `_xqat_request_id`、`_xqat_page_number`、`_xqat_row_number` provenance；
- `manifest.json`：Artifact 元数据、文件 hash 和日期范围。

gzip 使用固定 mtime，JSON 使用 canonical serialization，保证相同业务输入可产生稳定文件内容。

`data check-raw` 校验 Manifest、注册字段和业务主键重复；不会修改 Raw Artifact。

## 3. Research Artifact

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

## 4. 价格与单位

Raw 层保存 provider 值和单位来源。Research 层把成交量转换为 shares、金额转换为 CNY，并生成 raw 与 research 两套价格。

`research_open/high/low/close` 使用 adjustment factor 转换，供趋势和跨期研究；执行和真实成本相关逻辑使用 `open_raw/close_raw`。

## 5. 财务与系统因子

ResearchBuilder 按公告日和 available_from 选择当时可见的财务版本，派生季度利润、TTM 利润和年化 ROE。

系统因子由确定性算法计算，并记录 factor version、factor date、available_from、input start/end date 和 quality flags。ResearchCheck 明确检查 `input_end_date <= factor_date` 和 `available_from <= factor_date`。

## 6. ResearchSession

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

Execution 查询与研究查询分离：`execution_rows` 为指定执行日提供 raw 开高低收、估值 close、停牌/涨跌停、lot size 和 price tick；`corporate_actions` 提供账户事件日期；`benchmark_close` 固定读取沪深300指数 `000300.SH`。

## 7. Readiness

ReadinessChecker 优先按 StrategyDeclaration.data_requirements 评估。TRADING_DAYS 用实际天数计算 coverage；其他 requirement 通过 ResearchDataView 的 requirement coverage 查询实际数据覆盖。

required requirement 低于 minimum coverage 时，返回 declaration 指定的 failure code。Custom factor 在策略要求但未提供时 coverage 为 0。

## 8. 自定义因子

用户自定义因子是 CSV，固定列：

`factor_name,security_id,factor_date,factor_value`

CustomFactorCheck 检查证券、日期、重复、数值与覆盖。当前只有 weekly allocation 策略支持可选 custom factor；启用后 ResearchSession 仍只负责系统 Research，CSV 由 `CsvCustomFactorView` 独立加载并按 decision date 限制可见性。

## 9. Build 与 Update

`data build` 从 Raw roots 全量生成 Research Artifact。`data update` 读取一个已验证 Research base，再合并新 Raw 事实并重新计算受影响的状态和系统因子。无变化 update 仍重新发布完整、可独立验证的 Research Artifact，不制造隐式 latest 指针。
