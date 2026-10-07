# 工程与运行基线

## 1. 运行时

项目运行时固定为 CPython 3.12，`pyproject.toml` 要求 `>=3.12,<3.13`。

推荐使用 Conda：

```bash
conda create -n xqat python=3.12 -y
conda activate xqat
```

运行依赖由 `requirements.lock` 锁定；构建后端由 `requirements-build.lock` 锁定；测试和开发工具由 `requirements-dev.lock` 锁定。

项目是 setuptools `src/` 布局，console script 为 `xqatexp = xqatexp.cli:main`。wheel 同时包含顶层 `schemas/*.json`。

## 2. 平台目标

项目采用 Linux 优先的双平台策略：

- Linux x86_64：主平台和阻断性 CI 平台；
- Windows 10/11 x64：兼容性目标，CI 为 advisory；
- Python：CPython 3.12。

CI 在 ubuntu-latest / windows-latest 上使用 CPython 3.12。wheel 为 py3-none-any；
DuckDB、NumPy、PyArrow 等依赖包含各平台原生二进制，平台适用性还取决于锁定依赖可安装性。

Linux 与 Windows 应分别创建自己的 Conda 环境，不共享环境目录。WSL 的 Linux 验证优先使用 WSL 原生 ext4 工作区。

## 3. 核心依赖

运行时直接依赖：

- DuckDB >=1.2,<2
- httpx >=0.27,<1
- jsonschema >=4.23,<5
- NumPy >=2,<3
- PyArrow >=18,<24

Tushare 不作为 Python SDK 依赖；Provider 使用 HTTP API client。

## 4. 本地资源边界

ResearchSession：

- DuckDB memory limit 1GB；
- worker threads 为 1–4；
- session temp directory 位于显式输出父目录；
- 创建 session 前要求该文件系统至少 1GB 空闲。

ArtifactPublisher：

- 发布前要求输出父目录至少 1GB 空闲；
- staging 完成后还要求至少能容纳一份额外输出大小；
- 所有 rename/backup/recovery 都在同一父目录进行，避免跨卷原子性假设。

## 5. 安全基线

- Tushare Token 只从当前进程 `TUSHARE_TOKEN` 读取；
- Token 使用 SecretValue 包装，日志/结构化输出执行 redaction；
- 配置不允许 secret 类 key；
- 输入和输出路径必须互斥；
- Artifact 输出路径不允许经过 symlink/junction；
- result/log/failure-report 不能与输入路径重叠。

## 6. CI

`.github/workflows/cross-platform.yml` 当前矩阵：

- Ubuntu latest：`Linux (primary)`，失败阻断 workflow；
- Windows latest：`Windows (compatibility)`，job 允许失败。

两端均使用 CPython 3.12 和锁定依赖。

当前 CI 步骤：

1. 安装 build/dev locks 和 editable package；
2. Ruff code lint：advisory，只检查 `src/xqatexp tests`；
3. Mypy strict：advisory，只检查 `src/xqatexp`；
4. Offline code pytest：阻断，排除 live_tushare 和 documentation text test，coverage floor 80%；
5. CLI help：阻断；
6. offline self-check：阻断。

CI 不执行纯格式化检查。

## 7. 本地验证

完整开发依赖：

```bash
python -m pip install --require-hashes -r requirements-dev.lock
```

推荐本地代码验证：

```bash
conda activate xqat
python -m pytest -m "not live_tushare" --cov=xqatexp --cov-fail-under=80 -q
python -m ruff check src/xqatexp tests
python -m mypy src/xqatexp
python -m xqatexp self-check --offline
```

Live Tushare 测试必须由用户显式设置 `TUSHARE_TOKEN` 并传 `--live-tushare`，不会被默认 pytest 或离线 CI 隐式触发。

## 8. 确定性与跨平台文件行为

Canonical JSON 固定 key 排序与十进制表示；Raw gzip 固定 mtime；Parquet 表使用明确 schema、主键和排序规则。Artifact 使用 pathlib 和同目录 rename，不依赖 POSIX-only shell 命令。

跨平台支持不意味着 Linux 与 Windows 共享同一个 Conda 环境或运行中的临时目录；只要求相同输入和配置在受支持平台遵守相同领域合同和 Artifact Schema。

## 9. 数据运行与验证边界

批量获取默认 4 workers（范围 1–8），与 ResearchSession 的 DuckDB threads 分别配置。
全局预算包含分页、重试和 VIP 探测；provider-5000.toml 设置 420/min，限流不跨进程协调。
Collection 使用 Linux fcntl / Windows msvcrt 单写入锁，索引和报告使用相对路径和同目录原子替换。

本地测试覆盖日历假日、bootstrap/update 续跑、损坏补抓、孤立代次恢复、权限/分页失败、财务冲突、
普通/VIP Research 等价、路径安全、单写入锁和 CLI 构建。普通 pytest 不访问真实 Tushare。
网络、权限和性能验证需显式执行数据命令或 live 测试；用 batch report 的请求、限流、网络、
退避、发布和总耗时衡量，不将模拟延迟或平台目标写成真实运行保证。

独立文档检查包含 README、产品范围、架构、详细设计和两个策略 README 的链接及代码块。
文档内容与当前代码、Schema、CLI 和测试同步维护。
