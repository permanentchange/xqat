# 数据操作指南

所有命令从仓库根目录执行，使用 `conda activate xqat`。日期为示例范围；下载前按研究需求调整。
输出数据使用 `data/`，本地结果和检查报告使用 `.local/`。

## 凭据与能力检查

Token 仅由当前进程的 `TUSHARE_TOKEN` 注入：

```bash
export TUSHARE_TOKEN="<在本机填写你的 Token>"
```

```powershell
$env:TUSHARE_TOKEN = "<在本机填写你的 Token>"
```

```bash
xqatexp data capabilities --trade-date 2026-09-04 --provider-config examples/provider-5000.toml --output .local/tushare-capabilities.json
```

capabilities 探测注册表的普通接口，不探测 VIP 分页；VIP 在首次实际下载时单独验证。
未指定 `--trade-date` 时使用上一个工作日，可能碰到节假日；排查权限时显式给出已知交易日。
积分配置不是权限证明，权限错误不会自动使用其他来源替代。

## Provider 配置

[provider-5000.toml](../examples/provider-5000.toml) 是独立 TOML，供 fetch、fetch-batch 和 capabilities 使用。

| 字段 | 默认值 | 约束 |
| --- | ---: | --- |
| calls_per_minute | 200 | 有限正数；5000 积分示例设为 420 |
| workers | 4 | 整数 1–8；控制批次并发和连接池大小 |
| max_attempts | 5 | 整数 1–8；包含首次请求 |
| timeout_seconds | 30 | 有限正数，HTTP timeout |
| api_limits | 空表 | 注册 API 名称到有限正数频次的映射 |

所有 worker 共用 HTTP Client 和全局预算；单接口预算与全局预算同时生效。
分页、重试和 VIP 探测均计入请求次数。限流只覆盖当前进程；共享 Token 的其他进程需要自行分配预算。
429/业务限频触发共享冷却；5xx、timeout 和网络故障指数退避并加 jitter。
权限、认证、参数和 schema 错误不重复重试，provider 错误原文不会写入报告。

## Batch plan

[fetch-bootstrap.toml](../examples/fetch-bootstrap.toml) 包含日频、财务和显式辅助请求。

| 字段 | 用途 |
| --- | --- |
| start / end | 必填，交易日历覆盖范围，包含端点 |
| datasets | 必填非空数组，股票日频或 canonical 财务 dataset，不能重复 |
| period_start / period_end | 财务范围，包含端点；至少覆盖一个季度末 |
| refresh_days | update 刷新最近交易日数量，默认 5；0 表示关闭窗口刷新 |
| refresh_periods | update 刷新最近报告期数量，默认 8；0 表示关闭窗口刷新 |
| requests | 普通辅助请求数组；每项允许 dataset、start、end、security_id |

股票日频为 stock_daily、stock_adj_factor、stock_daily_basic、stock_price_limit、stock_suspend、stock_st_status。
income 和 fina_indicator 在 batch 中分别调用 income_vip 和 fina_indicator_vip。
财务按 3/31、6/30、9/30、12/31 分区，与交易日范围分别配置。

批次自动获取 SSE 日历，只为真实开市日期规划日频任务。requests 的日期默认继承 start/end；
fund_daily、fund_adj_factor、index_daily 按交易日分区且范围须在日历范围内。
trade_calendar 是自动依赖，不能作为显式请求。辅助证券请求一次指定一个 security_id。
dividend 的 security_id="*" 从 stock_basic 展开所有证券，包括退市股票，仍调用逐股票普通接口。
stock_basic 使用 SSE/SZSE 与 L/D/P/G 状态切片，fund_basic 使用场内基金 L/D 切片。

## Bootstrap 与续跑

```bash
xqatexp data fetch-batch --plan examples/fetch-bootstrap.toml --provider-config examples/provider-5000.toml --output data/raw/collection --dry-run
xqatexp data fetch-batch --plan examples/fetch-bootstrap.toml --provider-config examples/provider-5000.toml --output data/raw/collection
```

`--dry-run` 不联网、不要求 Token、不写文件。未取得已验证日历时，只展示范围、财务期和未解析依赖；
有日历后列出精确任务。列表中的分区数不等于实际 HTTP 次数；分页、空终止页、切片和重试都会增加请求。

bootstrap 在请求前验证已有分区的 Manifest、文件哈希、注册字段、业务键和请求身份，成功分区跳过。
缺失或损坏分区写入新代次，旧文件不原地覆盖。Ctrl+C 后使用相同 plan、mode 和输出目录续跑。
同一 Collection 只允许一个写入进程，锁在进程退出后释放；不要删除锁文件来绕过其他写入者。

批次目录：

```text
collection/
├── .collection.lock
├── collection.json
├── batch-result.json
└── artifacts/<partition-hash>/<generation-id>/
    ├── manifest.json
    ├── request.json
    └── response.jsonl.gz
```

索引/报告定期原子保存。请求完成但尚未索引的 Raw 代次可在续跑时恢复。
报告任务状态为 succeeded、skipped、failed、not_executed 或 interrupted；记录 attempts、row_count、
时间、error_code/category、HTTP/provider code。metrics 记录请求、分页、重试、限流等待、网络、
退避、发布、总耗时及实际每分钟请求数。路径为相对路径，目录整体移动后可继续读取。

## 增量更新

保留 plan 历史起点，延长 end 和所需 period_end，再同步 Research config 的结束日期：

```bash
xqatexp data fetch-batch --plan examples/fetch-bootstrap.toml --provider-config examples/provider-5000.toml --output data/raw/collection --mode update
xqatexp data build --raw-collection data/raw/collection --config examples/config-collection.toml --output data/research/weekly-collection-next
```

更新按 dataset 补洞、抓取新日期，并重抓最近 refresh_days 个交易日、refresh_periods 个报告期。
日历、证券列表及显式快照也刷新。相同未完成 update 的已成功任务跳过；已完成 update 再运行会重新刷新窗口。
财务窗口外修订需要扩大刷新范围。行情取最新有效代次；财务保留不同披露版本，相同事实去重。
同业务键出现不同内容时报 DATA_CONFLICT，不生成虚构的公告日或修订时间。

Raw 获取是增量的；`data build --raw-collection` 从完整历史重建 Research。
`data update --base --raw-root` 是独立的 Research 合并路径，接受已验证 base 和显式 Raw roots，
不接受 --raw-collection，也不负责联网下载。

## Research 构建与检查

```bash
xqatexp data build --raw-collection data/raw/collection --config examples/config-collection.toml --output data/research/weekly-collection
xqatexp data check-research --input data/research/weekly-collection --report .local/weekly-collection-check.json
```

--raw-collection 接受目录或其 collection.json，与重复 --raw-root 互斥。
Collection 必须完整、非空且所有选中代次可验证；构建成功并不保证任何策略满足 readiness。
周频策略需至少 313 个交易日 warmup、财务 TTM 历史和下一执行日行情；staged 策略按自己的价格窗口检查。
Research 输出固定七张表，来源不影响 canonical dataset、单位转换和公告可见性。

## 单次 Raw 与目录索引

```bash
xqatexp data fetch --dataset stock_daily --start 2026-09-04 --end 2026-09-04 --provider-config examples/provider-5000.toml --output data/raw/daily-20260904
xqatexp data check-raw --input data/raw/daily-20260904 --report .local/daily-check.json
xqatexp data fetch --dataset income --api income_vip --period 2026-06-30 --start 2026-06-30 --end 2026-06-30 --provider-config examples/provider-5000.toml --output data/raw/income-20260630
```

普通 income、fina_indicator、dividend、fund_daily、fund_adj_factor 需单个 --security-id。
VIP 需季度末 period、start=end=period，且不接受证券过滤。首次 VIP 调用验证 limit=1 和相邻 offset；
空期或不足两条记录不能确认分页，使用已有充分披露的报告期。分页按实际行数推进直到空页，
重复行、未推进 offset、字段变化或页数上限耗尽都会失败。

单次 fetch 默认 --existing error。skip 校验已有 Artifact 与请求匹配后跳过；overwrite 使用原子覆盖发布。
已有 Raw 目录可直接生成索引：

```bash
xqatexp data collection index --input data/raw/imported
xqatexp data build --raw-collection data/raw/imported --config examples/config-collection.toml --output data/research/imported
```

导入拒绝含混的重复/重叠范围，不会把失败批次的部分目录声明为完整。
单份 daily 不足以提供证券主表、日历和完整执行事实；按目标策略准备所需 Raw。

## 单证券 ETF 回测

staged_drawdown_v1 可以只准备交易日历、场内基金列表、目标 ETF 日线/复权因子及基准指数：

```bash
xqatexp data fetch --dataset trade_calendar --start 2024-01-01 --end 2026-09-10 --provider-config examples/provider-5000.toml --output data/raw/staged-etf/trade-calendar
xqatexp data fetch --dataset fund_basic --start 2024-01-01 --end 2026-09-10 --provider-config examples/provider-5000.toml --output data/raw/staged-etf/fund-basic
xqatexp data fetch --dataset fund_daily --security-id 510300.SH --start 2024-01-01 --end 2026-09-10 --provider-config examples/provider-5000.toml --output data/raw/staged-etf/fund-daily
xqatexp data fetch --dataset fund_adj_factor --security-id 510300.SH --start 2024-01-01 --end 2026-09-10 --provider-config examples/provider-5000.toml --output data/raw/staged-etf/fund-adj
xqatexp data fetch --dataset index_daily --start 2024-01-01 --end 2026-09-10 --provider-config examples/provider-5000.toml --output data/raw/staged-etf/index-daily
xqatexp data collection index --input data/raw/staged-etf
xqatexp data build --raw-collection data/raw/staged-etf --config examples/config-research-etf.toml --output data/research/staged-etf
xqatexp data check-research --input data/research/staged-etf --report .local/staged-etf-check.json
xqatexp backtest run --config examples/config-staged-etf.toml --output .local/staged-etf-backtest
xqatexp result show --input .local/staged-etf-backtest --format markdown
```

[Research 配置](../examples/config-research-etf.toml) 和 [回测配置](../examples/config-staged-etf.toml)
分别定义数据日期与回测日期。示例行情覆盖 2024-01-01 至 2026-09-10，回测为 2025-01-01 至 2026-09-01。
使用单行 CLI，不依赖 Bash 循环或 heredoc；Linux 与 Windows PowerShell 使用同一套流程。
这条路径不获取 ETF 分红，公司行为表可能为空；回测不会自动发现缺失事件，也不会模拟其现金或持仓变化。

## 错误处理

| 情况 | 操作 |
| --- | --- |
| 缺少 Token | 在运行命令的进程中注入 TUSHARE_TOKEN |
| 权限/认证失败 | 核对账号及接口权限；VIP 不自动退回普通接口 |
| DATA_COLLECTION_BUSY | 等待现有写入进程结束，再执行相同命令 |
| 批次 complete=false | 查看 batch-result.json；修复原因后续跑，不构建部分 Collection |
| 分页/schema 失败 | 核对接口字段和分页支持；原始响应不会发布为成功分区 |
| DATA_CONFLICT | 检查相同业务键的事实差异，不通过伪造披露日期消除冲突 |
| 输出已存在 | 使用新输出路径，或对支持的 Artifact 命令明确指定 --existing |
| 数据/readiness 不足 | 补充目标策略的日历、行情、财务或因子历史，再构建并检查 |

检查报告、capabilities 和 state 命令要求新输出文件；batch 的 index/report 由协调线程原子更新。
Collection 禁止路径逃逸、symlink/junction 以及内部 Raw 文件链接。
