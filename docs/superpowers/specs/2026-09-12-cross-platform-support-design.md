# XQatExp Linux 优先的双平台支持设计

日期：2026-09-12
状态：已获用户设计批准，待规格复核
上位输入：`PRD.txt`、`总体架构设计.md`（只读，不修改）

## 1. 目标

将 XQatExp 从“Windows 正式支持、Linux 仅兼容性测试”提升为可在 Linux 与 Windows 原生运行的纯 Python 工具，并以 Linux 作为首选运行环境。

本次交付必须证明：

1. Linux x86_64 上能够从干净环境安装、运行 CLI、完成离线端到端流程并执行真实 Tushare 冒烟测试；
2. Windows 10/11 x64 的 PowerShell 使用方式保持可用，并通过同等级回归验证；
3. 两个平台遵守相同业务合同、配置、Artifact 格式、确定性和安全边界；
4. 文档默认先展示 Linux，再展示 Windows PowerShell；
5. 仓库不保存 Tushare Token，PRD 和总体架构设计保持字节不变。

## 2. 方案选择

### 2.1 采用方案：原生双平台支持

使用同一 Python 包和同一 CLI，在 Linux 与 Windows 上分别使用标准 `venv`。平台差异仅保留在文件系统语义、虚拟环境入口和用户命令表示层；业务逻辑、数据合同和结果格式不分叉。

选择原因：

- 符合“纯 Python 工具”和 Linux 优先的目标；
- 不要求 Docker、后台服务或新的部署体系；
- 能直接验证 Linux POSIX 文件系统和 Windows NTFS 的实际行为；
- 保留当前 Windows 用户的运行方式。

### 2.2 未采用方案

- **仅补 Linux 文档**：不能证明依赖、路径和原子发布在 Linux 上真实可用。
- **容器作为唯一 Linux 入口**：增加额外运行时，偏离本地纯 Python 工具定位，也无法替代 Windows 原生验证。
- **放宽到 Python 3.10**：会扩大依赖与行为矩阵，并与既有 CPython 3.12 工程基线冲突；当前 WSL 应安装独立 Python 3.12，而不是降低项目要求。

## 3. 正式支持矩阵

| 维度 | Linux | Windows |
|---|---|---|
| 优先级 | 首选运行平台 | 兼容且正式支持 |
| 架构 | x86_64 | x64 |
| Python | CPython `>=3.12,<3.13` | CPython `>=3.12,<3.13` |
| 实际验收基线 | Ubuntu 22.04.5 LTS，WSL2，glibc 2.35 | Windows 11 x64，PowerShell |
| 最低 Linux ABI | glibc 2.28 级别 | 不适用 |
| 环境 | 标准库 `venv` | 标准库 `venv` |
| CLI | `.venv/bin/xqatexp` 或 `.venv/bin/python -m xqatexp` | `.venv\Scripts\xqatexp.exe` 或 `.venv\Scripts\python.exe -m xqatexp` |
| 路径 | POSIX 路径、符号链接语义 | Windows 路径、junction 语义 |

Ubuntu 22.04 自带的 Python 3.10 不作为运行时。WSL 验收安装独立 CPython 3.12，不替换系统 `python3`，随后创建隔离的标准 `venv`。

## 4. 环境与工作区规则

普通用户在各自平台的独立仓库副本中使用项目根目录 `.venv`：

- Linux：`.venv/bin/python`、`.venv/bin/xqatexp`；
- Windows：`.venv\Scripts\python.exe`、`.venv\Scripts\xqatexp.exe`。

Windows venv 与 Linux venv 不可互用。若同一仓库通过 `/mnt/c` 同时被 Windows 与 WSL 访问，不得让 WSL 使用 Windows `.venv`。正式 Linux 验收在 WSL 的原生 ext4 文件系统中创建干净仓库副本和 `.venv`，避免 DrvFS 行为掩盖 POSIX 权限、rename 或符号链接问题。

不引入 Conda、Docker、全局 site-packages 或项目外持久服务。

## 5. 依赖与打包设计

`pyproject.toml` 继续作为包元数据和直接依赖权威来源，Python 范围保持 `>=3.12,<3.13`。三个带哈希锁文件继续分别承担：

- `requirements.lock`：运行时；
- `requirements-build.lock`：构建后端；
- `requirements-dev.lock`：测试和开发门禁。

验收必须在两个平台分别从锁文件安装。Linux 解析需覆盖目标 glibc 可接受的 manylinux 标签集合，而不是把单个 wheel 标签误认为完整 ABI 能力。

已完成的预实施取证显示，三套锁文件均存在 CPython 3.12/Linux x86_64 可接受的 wheel，包括 DuckDB、NumPy、PyArrow、rpds-py、Ruff 和 mypy。该静态解析只能证明候选包可获得，不能替代 WSL 中的真实安装。

构建出的 wheel 必须保持平台无关（`py3-none-any`），并继续携带 JSON Schema 数据文件。不得把 Windows 启动器、绝对路径或 Token 写入发行包。

## 6. 平台相关实现设计

### 6.1 路径与输入输出

- 生产代码统一使用 `pathlib.Path`，不拼接硬编码分隔符；
- Artifact Manifest 内部成员名继续使用 `/` 规范化，并在读取时转换为本地路径；
- 所有用户路径先进行词法安全检查，再解析绝对路径；
- Linux 拒绝符号链接逃逸，Windows 同时拒绝符号链接和 junction；
- 路径错误消息可以显示本地路径，但业务 Issue code 和退出码必须一致。

### 6.2 Artifact 发布与恢复

两个平台共享同一安全后置条件：正式路径不存在，或指向一个 Manifest 与全部摘要验证通过的完整 Artifact。

- 新目标：在同一父目录构建 staging，完整校验后 rename 到不存在的正式路径；
- 覆盖目标：继续使用 staging、swap 记录、backup 和恢复状态机；
- Linux 必须实际覆盖 POSIX rename、符号链接和中断恢复路径；
- Windows 必须继续覆盖 NTFS rename、占用/失败回滚和 junction 防护；
- 清理只能针对解析后且名称精确匹配本次目标的临时路径，禁止宽泛 glob。

现有通用实现若在新增测试中全部满足上述合同，不为制造平台分支而重构；只有失败测试证明存在差异时才修改生产代码。

### 6.3 文本、时间与进程

- 文本继续统一为 UTF-8，无 BOM，仓库文件使用 LF；
- 时区继续显式使用 `Asia/Shanghai`，持久化 datetime 使用 UTC；
- 子进程测试使用当前解释器 `sys.executable`，不依赖 `python`、`py`、`.exe` 或 shell 激活状态；
- 业务命令不启动服务、守护进程或隐式网络请求。

## 7. 文档设计

更新范围：

- 根目录 `README.md`：Linux 安装和完整使用流程在前，Windows PowerShell 对应命令在后；说明 WSL/Windows venv 不可共享；覆盖 Token 设置、数据、Research、回测、Daily、结果查看和开发校验；
- `docs/detailed-design/17-engineering-baseline.md`：把 Linux 提升为正式支持并定义平台矩阵、独立环境和双平台验收；
- 与安全、测试、验证直接相关的详细设计按需同步，确保没有“Linux 仅兼容性测试”等冲突描述；
- 新增独立跨平台验证报告，记录命令、平台版本、结果和限制；历史验证报告保留为当时证据，不伪造或改写历史。

不修改 `PRD.txt`、`总体架构设计.md` 和用户提供的输入数据。

## 8. 测试驱动实施

先添加或强化失败测试，再按最小修改使其通过。测试至少覆盖：

1. 文档合同声明 Linux 优先、Windows 正式支持，并提供两套可复制入口；
2. package metadata 不包含平台限定或 Windows 绝对路径；
3. CLI 的 console script 与 `python -m xqatexp` 在两个平台均可启动；
4. POSIX 路径、空格路径、非 ASCII 路径和本地分隔符处理；
5. Linux 符号链接拒绝与安全输出路径；
6. Artifact 新发布、显式覆盖、失败回滚和恢复；
7. 生成文件的 UTF-8、Manifest `/` 成员名和跨平台确定性；
8. 环境变量 Token 的读取、缺失报错和全出口脱敏。

测试不得仅用 `platform.system()` 模拟成功；平台敏感行为必须在对应操作系统真实执行。

## 9. 验收流程

### 9.1 Linux（首要门禁）

在 WSL Ubuntu 22.04 的原生 Linux 文件系统中：

1. 从当前提交创建干净仓库副本；
2. 使用独立 CPython 3.12 创建 `.venv`；
3. 按哈希锁文件安装运行、构建和开发依赖；
4. 构建 wheel，并在干净运行环境中安装；
5. 执行 Ruff format、Ruff lint、mypy 和非真实网络 pytest 全套门禁；
6. 执行 CLI help、offline self-check 和 README 完整离线端到端流程；
7. 使用只存在于测试进程环境中的 `TUSHARE_TOKEN` 执行真实冒烟测试；
8. 扫描仓库、发行包、输出和日志，确认不含 Token。

### 9.2 Windows

在 Windows PowerShell 和独立 CPython 3.12 venv 中执行同等级门禁、安装、CLI、离线端到端和真实 Tushare 冒烟测试。

### 9.3 一致性

对固定离线输入比较两个平台的结构化业务结果。忽略平台合理差异（例如绝对路径、运行时间、run id）后，策略目标、净值、成交、指标、Issue code 和 Manifest 成员集合必须一致。

## 10. 安全与秘密

- Token 只从当前测试进程的 `TUSHARE_TOKEN` 读取；
- 不在命令行参数、配置、源码、文档、日志、Artifact、Git diff 或提交中保存真实 Token；
- WSL 传递 Token 时只做临时进程环境桥接，用后立即清除；
- 最终执行仓库历史和当前工作区秘密扫描；
- 真实测试保持显式 `--live-tushare` 开关，离线测试继续禁止网络。

## 11. 完成条件

仅当以下证据全部成立才可声明完成：

- Linux 与 Windows 的干净安装均成功；
- 两个平台的全量离线测试和质量门禁均成功；
- 两个平台的离线端到端流程均成功且关键业务结果一致；
- 两个平台的真实 Tushare 冒烟测试均成功；
- README 和详细设计无平台支持冲突；
- PRD 与总体架构设计哈希保持不变；
- Token 未出现在仓库、提交或交付产物；
- 工作区干净，所有本次改动已提交。
