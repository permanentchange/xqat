from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import unquote

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MARKDOWN_FILES = (
    PROJECT_ROOT / "README.md",
    PROJECT_ROOT / "PRD.txt",
    PROJECT_ROOT / "总体架构设计.md",
    *sorted((PROJECT_ROOT / "docs").rglob("*.md")),
    *sorted((PROJECT_ROOT / "src/xqatexp/strategy/strategies").glob("*/README.md")),
)


def _slug(heading: str) -> str:
    value = re.sub(r"[`*_]", "", heading.strip().lower())
    value = re.sub(r"[^\w\- ]", "", value)
    return value.replace(" ", "-")


def test_readme_documents_current_public_commands() -> None:
    from xqatexp.cli import _build_parser

    readme = (PROJECT_ROOT / "README.md").read_text("utf-8")

    def check_commands(parser: argparse.ArgumentParser, prefix: str = "") -> None:
        subcommands = [
            action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
        ]
        if not subcommands:
            assert prefix in readme, f"README missing command: {prefix}"
        for action in subcommands:
            for name, child in action.choices.items():
                check_commands(child, f"{prefix} {name}".strip())

    check_commands(_build_parser())


def test_markdown_local_links_resolve_to_files_and_headings() -> None:
    link_pattern = re.compile(r"\[[^]]*]\(([^)]+)\)")
    for source in MARKDOWN_FILES:
        content = source.read_text(encoding="utf-8")
        for raw_target in link_pattern.findall(content):
            target_text = raw_target.strip().strip("<>")
            if re.match(r"^[a-z]+://", target_text) or target_text.startswith("#"):
                continue
            file_part, _, fragment = target_text.partition("#")
            target = (source.parent / unquote(file_part)).resolve()
            assert target.is_file(), f"{source}: missing {target_text}"
            assert PROJECT_ROOT.resolve() in (target, *target.parents)
            if fragment and target.suffix.lower() == ".md":
                headings = {
                    _slug(line.lstrip("# "))
                    for line in target.read_text(encoding="utf-8").splitlines()
                    if line.startswith("#")
                }
                assert unquote(fragment).lower() in headings, f"{source}: missing #{fragment}"


def test_markdown_fences_mermaid_and_image_policy_are_valid() -> None:
    allowed_mermaid = ("flowchart", "sequenceDiagram", "stateDiagram-v2")
    for source in MARKDOWN_FILES:
        lines = source.read_text(encoding="utf-8").splitlines()
        assert sum(line.startswith("```") for line in lines) % 2 == 0, source
        for index, line in enumerate(lines):
            if line.strip() == "```mermaid":
                declaration = next(value.strip() for value in lines[index + 1 :] if value.strip())
                assert declaration.startswith(allowed_mermaid), f"{source}: {declaration}"
        assert not re.search(r"\.(?:jpe?g)(?:\W|$)", "\n".join(lines), re.IGNORECASE), source


def test_production_source_has_no_unfinished_placeholders() -> None:
    pattern = re.compile(r"\b(?:TODO|FIXME|TBD|NotImplemented)\b|not implemented yet|^\s*pass\s*$")
    findings = []
    for source in sorted((PROJECT_ROOT / "src").rglob("*.py")):
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                findings.append(f"{source.relative_to(PROJECT_ROOT)}:{number}")
    assert findings == []


def test_engineering_baseline_documents_platform_and_runtime_targets() -> None:
    baseline = (PROJECT_ROOT / "docs/detailed-design/17-engineering-baseline.md").read_text(
        encoding="utf-8"
    )
    assert "Linux x86_64" in baseline
    assert "Windows 10/11 x64" in baseline
    assert "CPython 3.12" in baseline
    assert "ubuntu-latest" in baseline
    assert "windows-latest" in baseline
    assert "Linux 优先" in baseline
    assert "Linux x86_64 用于兼容性测试" not in baseline
