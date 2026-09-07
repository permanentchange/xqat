from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import unquote

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_HASHES = {
    "PRD.txt": "2CC90E04C56ADFCF4B9FA73354D3EF4D6DBE5B88FAE1B23D938D780D185D1148",
    "总体架构设计.md": "32D425F69DB9DB02D80079C622BD60B79E2C07780CC45C592A8A2A3C55DF4C15",
}
MARKDOWN_FILES = (PROJECT_ROOT / "README.md", *sorted((PROJECT_ROOT / "docs").rglob("*.md")))


def _slug(heading: str) -> str:
    value = re.sub(r"[`*_]", "", heading.strip().lower())
    value = re.sub(r"[^\w\- ]", "", value)
    return value.replace(" ", "-")


def test_upstream_inputs_match_approved_baseline_hashes() -> None:
    for name, expected in UPSTREAM_HASHES.items():
        assert hashlib.sha256((PROJECT_ROOT / name).read_bytes()).hexdigest().upper() == expected


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
