# Linux-First Cross-Platform Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make XQatExp a verified Linux-first pure Python tool that also remains fully usable from Windows PowerShell.

**Architecture:** Keep one platform-neutral Python package, one CLI, and one set of business contracts. Express platform differences only in user commands and filesystem acceptance tests; retain the existing recovery protocol unless a real Linux or Windows test exposes a defect. Verify Linux in a clean WSL ext4 checkout and Windows in PowerShell, then compare deterministic outputs.

**Tech Stack:** CPython 3.12, standard-library `venv`, pathlib, setuptools, pytest, Hypothesis, Ruff, mypy, WSL2 Ubuntu 22.04, PowerShell.

**Spec:** `docs/superpowers/specs/2026-09-12-cross-platform-support-design.md`

## Global Constraints

- Linux x86_64 and Windows 10/11 x64 are both formally supported; Linux is the preferred platform.
- CPython is fixed to `>=3.12,<3.13`; do not weaken the version requirement to accommodate Ubuntu's system Python 3.10.
- Linux verification baseline is Ubuntu 22.04.5 LTS on WSL2 with glibc 2.35; the minimum declared Linux ABI is glibc 2.28.
- Use standard-library `venv`; do not add Conda, Docker, services, global site-packages, or new runtime dependencies.
- Windows and Linux virtual environments are separate and must never be reused across platforms.
- Run Linux filesystem acceptance on WSL's native ext4 filesystem, not only under `/mnt/c`.
- Keep `PRD.txt` and `总体架构设计.md` byte-identical to hashes `2CC90E04C56ADFCF4B9FA73354D3EF4D6DBE5B88FAE1B23D938D780D185D1148` and `32D425F69DB9DB02D80079C622BD60B79E2C07780CC45C592A8A2A3C55DF4C15`.
- Never persist the real Tushare Token in source, documentation, commands recorded in repository files, logs, artifacts, Git history, or reports.
- The user explicitly authorized direct work and commits on the current branch; preserve unrelated user changes if any appear.
- Follow red-green-refactor for every production behavior change. Acceptance tests that already pass document existing portable behavior and do not justify artificial production changes.

---

### Task 1: Lock the Linux-first documentation contract

**Files:**
- Modify: `tests/e2e/test_installation.py`
- Modify: `tests/contract/test_documentation.py`
- Modify: `README.md`
- Modify: `docs/detailed-design/17-engineering-baseline.md`
- Modify if conflicting text is found: `docs/detailed-design/13-security-and-operations.md`
- Modify if conflicting text is found: `docs/detailed-design/14-testing-and-traceability.md`
- Modify if conflicting text is found: `docs/detailed-design/15-verification-report.md`

**Interfaces:**
- Consumes: the support matrix and environment rules in the approved spec.
- Produces: executable Linux and PowerShell instructions plus automated documentation assertions used by later acceptance work.

- [ ] **Step 1: Add failing README support assertions**

Extend `test_readme_documents_every_public_command_and_secret_boundary` or add a focused test with these assertions:

```python
def test_readme_is_linux_first_and_keeps_windows_powershell() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.index("Linux") < readme.index("Windows PowerShell")
    assert ".venv/bin/python" in readme
    assert ".venv/bin/xqatexp" in readme
    assert r".\.venv\Scripts\python.exe" in readme
    assert r".\.venv\Scripts\xqatexp.exe" in readme
    assert "不能共享" in readme
    assert "Ubuntu 22.04" in readme
```

Add a documentation contract in `tests/contract/test_documentation.py`:

```python
def test_engineering_baseline_formally_supports_linux_and_windows() -> None:
    baseline = (PROJECT_ROOT / "docs/detailed-design/17-engineering-baseline.md").read_text(
        encoding="utf-8"
    )
    assert "Linux x86_64" in baseline
    assert "Windows 10/11 x64" in baseline
    assert "glibc 2.28" in baseline
    assert "Linux 优先" in baseline
    assert "Linux x86_64 用于兼容性测试" not in baseline
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\e2e\test_installation.py tests\contract\test_documentation.py -q
```

Expected: the new assertions fail because README is Windows-only and design 17 still calls Linux a compatibility-test platform.

- [ ] **Step 3: Rewrite the user guide with Linux first**

Restructure `README.md` so the first install and workflow commands use:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m pip install --require-hashes -r requirements-build.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation .
.venv/bin/xqatexp self-check --offline
```

Provide a complete PowerShell counterpart:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-build.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation .
.\.venv\Scripts\xqatexp.exe self-check --offline
```

Document that Windows `.venv` and Linux `.venv` cannot be shared, and show Linux `export TUSHARE_TOKEN="<在本机填写你的 Token>"` beside the PowerShell process-local assignment. Keep every existing public command discoverable.

- [ ] **Step 4: Align detailed design without rewriting historical evidence**

Change design 17 to declare Linux-first formal support and the exact matrix from the spec. Define the ext4 WSL acceptance rule and platform-separated venv rule. Update designs 13–15 only where needed to eliminate current-state contradictions; retain dated Windows-only results as explicitly historical evidence.

- [ ] **Step 5: Run documentation tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\e2e\test_installation.py tests\contract\test_documentation.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Check formatting, immutable inputs, and commit**

Run:

```powershell
git diff --check
Get-FileHash -Algorithm SHA256 PRD.txt,总体架构设计.md
git diff -- PRD.txt 总体架构设计.md
```

Expected: no whitespace errors, frozen hashes match Global Constraints, and no upper-input diff.

Commit:

```bash
git add README.md docs/detailed-design tests/e2e/test_installation.py tests/contract/test_documentation.py
git commit -m "docs: make Linux the preferred supported platform"
```

### Task 2: Add real cross-platform filesystem acceptance tests

**Files:**
- Modify: `tests/unit/test_publisher.py`
- Modify: `tests/contract/test_artifact_roundtrip.py`
- Modify only after a failing test: `src/xqatexp/artifacts/publisher.py`
- Modify only after a failing test: `src/xqatexp/artifacts/readers.py`

**Interfaces:**
- Consumes: `ArtifactPublisher.publish(...)`, `ArtifactReader.open(...)`, `OverwritePolicy`, and the shared Artifact recovery contract.
- Produces: OS-native regression coverage for spaces, non-ASCII paths, POSIX symlinks, nested Manifest members, overwrite, and recovery.

- [ ] **Step 1: Add an OS-native Unicode and space path round trip**

Add a contract test that publishes into `tmp_path / "跨平台 result"`, writes a nested file `nested/payload.txt`, then asserts:

```python
published = ArtifactPublisher().publish(builder, output, OverwritePolicy.ERROR)
opened = ArtifactReader().open(published.path)
assert published.path == output.resolve()
assert "nested/payload.txt" in opened.verified_files
assert all("\\" not in member for member in opened.verified_files)
```

The builder must create the nested directory and a valid Manifest using existing test helpers rather than bypassing production validation.

- [ ] **Step 2: Add a real directory-symlink rejection test**

Add:

```python
def test_publisher_rejects_real_directory_symlink(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlink unavailable on this host: {error}")
    with pytest.raises(ArtifactPublishError, match="ARTIFACT_UNSAFE_OUTPUT_PATH"):
        ArtifactPublisher().publish(
            valid_builder("payload"), linked_parent / "result", OverwritePolicy.ERROR
        )
```

Use the existing builder helper's actual name/signature when applying the plan.

- [ ] **Step 3: Run the focused tests and classify the result**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_publisher.py tests\contract\test_artifact_roundtrip.py -q
```

Expected: either PASS, proving the current implementation already satisfies these acceptance contracts, or a behavior-specific FAIL. A pass is acceptable for characterization/acceptance tests and must not trigger artificial production changes.

- [ ] **Step 4: If and only if RED exposed a defect, implement the minimal fix**

For a separator defect, normalize Manifest members at the serialization boundary with `relative_path.as_posix()`. For a symlink defect, preserve the lexical path walk before `.resolve()`:

```python
lexical_target = Path(os.path.abspath(output_path))
for component in (lexical_target, *lexical_target.parents):
    is_junction = getattr(component, "is_junction", lambda: False)()
    if component.is_symlink() or is_junction:
        raise ArtifactPublishError(
            "ARTIFACT_UNSAFE_OUTPUT_PATH: symlink or junction in output path"
        )
```

Do not add platform-name branching if common pathlib behavior satisfies both operating systems.

- [ ] **Step 5: Verify GREEN and regression**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_publisher.py tests\contract\test_artifact_roundtrip.py tests\property\test_invariants.py -q
```

Expected: all selected tests pass; Windows may skip only the real symlink test when the OS denies symlink creation.

- [ ] **Step 6: Commit filesystem acceptance coverage**

```bash
git add tests/unit/test_publisher.py tests/contract/test_artifact_roundtrip.py src/xqatexp/artifacts/publisher.py src/xqatexp/artifacts/readers.py
git commit -m "test: cover cross-platform artifact filesystem behavior"
```

Omit unchanged production paths from `git add`.

### Task 3: Provision and validate a clean Linux environment

**Files:**
- No repository file is modified by environment provisioning.
- Create outside the repository: a user-local CPython 3.12 installation if necessary.
- Create outside the Windows checkout: an ext4 validation clone and Linux `.venv`.

**Interfaces:**
- Consumes: committed repository state, all three lock files, and the Ubuntu 22.04 WSL2 runtime.
- Produces: authoritative Linux installation, packaging, quality-gate, filesystem, CLI, and offline-E2E evidence.

- [ ] **Step 1: Install an independent CPython 3.12 without replacing system Python**

Because WSL has Python 3.10 and no non-interactive sudo, download a pinned Python 3.12 source release from `python.org`, verify its published SHA-256, build it with the available compiler and headers, and install under a user-owned prefix such as `$HOME/.local/xqatexp-python-3.12`. Do not alter `/usr/bin/python3`.

Before using it, verify:

```bash
$HOME/.local/xqatexp-python-3.12/bin/python3.12 --version
$HOME/.local/xqatexp-python-3.12/bin/python3.12 -c 'import ssl, sqlite3, bz2, lzma, ctypes; print("stdlib-ok")'
```

Expected: CPython 3.12.x and `stdlib-ok`. If required build headers are missing and cannot be installed without user credentials, stop and report the exact module/dependency rather than weakening the runtime.

- [ ] **Step 2: Clone committed state into WSL ext4**

Use a unique directory under `/tmp` or the WSL user's home, sourced from `/mnt/c/Users/zxfho/Desktop/XQAT`. Verify that `df -T` reports a native Linux filesystem rather than `9p`/DrvFS and that `git rev-parse HEAD` equals the Windows checkout commit.

- [ ] **Step 3: Create clean build and test environments**

In the ext4 clone:

```bash
$HOME/.local/xqatexp-python-3.12/bin/python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
.venv/bin/python -m pip install --require-hashes -r requirements-build.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
```

Expected: installation succeeds without global site-packages.

- [ ] **Step 4: Run Linux quality gates**

```bash
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src/xqatexp
.venv/bin/python -m pytest -m 'not live_tushare' --cov=xqatexp --cov-report=term-missing --cov-fail-under=90
```

Expected: all commands exit 0, no unexpected warnings, and the real Linux symlink test executes rather than skips.

- [ ] **Step 5: Build and inspect the wheel**

```bash
.venv/bin/python -m build --wheel --no-isolation
.venv/bin/python -c 'import glob, zipfile; p=glob.glob("dist/*.whl")[-1]; z=zipfile.ZipFile(p); assert p.endswith("py3-none-any.whl"); assert any(n.startswith("schemas/") for n in z.namelist()); print(p)'
```

Expected: a `py3-none-any` wheel containing the JSON schemas.

- [ ] **Step 6: Run Linux CLI and complete offline workflow**

Run the exact Linux commands documented in README, including help, `self-check --offline`, offline example generation, research check, daily target, daily advice, backtest, and result display. Use unique output paths or explicit documented overwrite semantics.

Expected: every command exits 0 and Artifact verification succeeds.

- [ ] **Step 7: If Linux exposes a defect, reproduce it on the Windows checkout before editing**

Add the smallest failing automated test to the committed test suite, run it on the platform where it fails, make the minimal implementation change, then rerun both the focused test and Task 3 gates. Commit each defect fix separately with a message naming the behavior.

### Task 4: Run Windows regression and compare deterministic outputs

**Files:**
- No repository files unless a failing test requires a TDD fix.
- Temporary validation outputs remain ignored and are not committed.

**Interfaces:**
- Consumes: the same committed state and fixed offline example inputs used on Linux.
- Produces: authoritative Windows PowerShell evidence and cross-platform result equivalence evidence.

- [ ] **Step 1: Run Windows quality gates from CPython 3.12**

```powershell
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy src\xqatexp
.\.venv\Scripts\python.exe -m pytest -m "not live_tushare" --cov=xqatexp --cov-report=term-missing --cov-fail-under=90
```

Expected: all commands exit 0; only an unavailable real-symlink test may be skipped.

- [ ] **Step 2: Run the documented PowerShell install and workflow**

Create a clean Windows acceptance venv at an exact validated path outside `.venv`, install from the three lock files without global packages, install the built wheel, and run every documented PowerShell workflow command.

Expected: CLI help, self-check, Research, Daily, Backtest, and result display all succeed.

- [ ] **Step 3: Compare Linux and Windows structured outputs**

Use a temporary Python comparison script or inline Python that loads both platform output directories, excludes only documented volatile fields (`run_id`, timestamps, absolute paths, elapsed durations), and asserts equality for:

```text
target_history.csv
nav_daily.csv
executions.csv
unfilled.csv
metrics.json
period_metrics.csv
issues.json
artifact_manifest.json member names and business metadata
```

Expected: normalized business data is identical. Any unexplained difference is a defect requiring a failing regression test before production changes.

### Task 5: Run real Tushare smoke tests on both platforms

**Files:**
- No repository files except the final report, written only after tests finish.

**Interfaces:**
- Consumes: the user's authorized Token through a process-local environment variable and the existing `live_tushare` test.
- Produces: one successful, minimal real API request on Linux and one on Windows without secret persistence.

- [ ] **Step 1: Run the Windows live smoke test**

Temporarily set `TUSHARE_TOKEN` in the PowerShell process, run:

```powershell
.\.venv\Scripts\python.exe -m pytest -m live_tushare --live-tushare -q
```

Clear the variable in `finally`. Expected: `1 passed`; never print the Token.

- [ ] **Step 2: Run the Linux live smoke test**

Bridge `TUSHARE_TOKEN` into one WSL process through temporary environment propagation, run in the ext4 clone:

```bash
.venv/bin/python -m pytest -m live_tushare --live-tushare -q
```

Remove the bridge and both host/process variables immediately afterward. Expected: `1 passed`; never put the Token in a shell history or repository file.

- [ ] **Step 3: Scan all relevant outputs for secret exposure**

Search tracked files, untracked project files, Git diff, built wheel contents, validation logs, and Artifact text for the known full secret and sufficiently long canary fragments. Expected: zero matches. Do not include the secret literal or its fragments in the committed report.

### Task 6: Publish cross-platform verification evidence and close the change

**Files:**
- Create: `docs/cross-platform-verification-report.md`
- Modify: `docs/detailed-design/15-verification-report.md`
- Modify: `README.md` only if actual acceptance required corrections.

**Interfaces:**
- Consumes: exact results from Tasks 1–5.
- Produces: a dated, reproducible, non-secret verification record and a clean final repository state.

- [ ] **Step 1: Write the verification report from observed facts**

Record:

- exact Windows and Linux OS/Python/filesystem versions;
- commit tested;
- dependency installation result;
- Ruff, mypy, pytest count/coverage and skip reasons;
- wheel filename and schema-presence result;
- offline CLI/E2E result;
- normalized cross-platform comparison result;
- Windows and Linux live Tushare smoke result without response payload or Token;
- immutable-input hashes and secret-scan result;
- any remaining environmental limitation stated narrowly.

Link the new report from design 15 while preserving its dated historical Windows section.

- [ ] **Step 2: Add report contract assertions**

In `tests/contract/test_documentation.py`, assert the report exists, is linked from design 15, names both verified platforms, contains the tested commit and quality-gate outcomes, and never contains a token-shaped 40–64 lowercase hexadecimal credential after a `TOKEN` label.

- [ ] **Step 3: Run focused documentation tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\contract\test_documentation.py tests\e2e\test_installation.py -q
```

Expected: all pass.

- [ ] **Step 4: Run final dual-platform gates again at report commit**

Repeat Task 3 Linux gates and Task 4 Windows gates against the exact final commit candidate. If the report commit itself changes only Markdown and tests, at minimum rerun documentation tests plus both full offline suites to prove no stale claim.

- [ ] **Step 5: Perform the completion audit**

Verify every completion condition in the approved spec with direct evidence. Also run:

```powershell
git diff --check
git diff -- PRD.txt 总体架构设计.md
Get-FileHash -Algorithm SHA256 PRD.txt,总体架构设计.md
git status --short
```

Expected: no whitespace errors, no upper-input changes, frozen hashes match, and only intended report/test changes remain before commit.

- [ ] **Step 6: Commit the report and final contracts**

```bash
git add docs/cross-platform-verification-report.md docs/detailed-design/15-verification-report.md tests/contract/test_documentation.py README.md
git commit -m "docs: record dual-platform acceptance results"
```

Omit unchanged files from `git add`.

- [ ] **Step 7: Verify the committed repository is clean and safe**

Run the immutable hash check, secret scan, `git status --short`, `git log --oneline` for all new commits, and both platforms' focused smoke commands. Expected: clean worktree, no secret match, and all required commits present.
