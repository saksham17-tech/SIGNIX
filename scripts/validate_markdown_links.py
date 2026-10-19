"""Validate local links and GitHub-style heading anchors in repository Markdown."""

from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
INLINE_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
REFERENCE_LINK_RE = re.compile(r"^\s*\[[^\]]+\]:\s*(\S+)", re.MULTILINE)
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
HTML_TAG_RE = re.compile(r"<[^>]+>")
INLINE_CODE_RE = re.compile(r"`+([^`]*)`+")
EXTERNAL_SCHEMES = {"http", "https", "mailto", "tel", "data"}


def markdown_files() -> list[Path]:
    """Return tracked and untracked, non-ignored Markdown files deterministically."""
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            "*.md",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return sorted(ROOT / line for line in result.stdout.splitlines() if line.strip())


def github_slug(value: str) -> str:
    """Approximate GitHub's documented Markdown heading slug generation."""
    value = INLINE_CODE_RE.sub(r"\1", HTML_TAG_RE.sub("", value)).strip().lower()
    value = re.sub(r"[^\w\- ]", "", value, flags=re.UNICODE)
    return value.replace(" ", "-")


def anchors(markdown: Path) -> set[str]:
    counts: Counter[str] = Counter()
    result: set[str] = set()
    for match in HEADING_RE.finditer(markdown.read_text(encoding="utf-8")):
        base = github_slug(match.group(2))
        slug = base if counts[base] == 0 else f"{base}-{counts[base]}"
        counts[base] += 1
        result.add(slug)
    return result


def link_targets(text: str) -> list[str]:
    targets = [match.group(1).strip() for match in INLINE_LINK_RE.finditer(text)]
    targets.extend(match.group(1).strip() for match in REFERENCE_LINK_RE.finditer(text))
    cleaned: list[str] = []
    for target in targets:
        if target.startswith("<") and ">" in target:
            target = target[1 : target.index(">")]
        elif " " in target:
            target = target.split(None, 1)[0]
        cleaned.append(target)
    return cleaned


def has_exact_case(path: Path) -> bool:
    try:
        relative = path.relative_to(ROOT)
    except ValueError:
        return False
    current = ROOT
    for part in relative.parts:
        if part not in {child.name for child in current.iterdir()}:
            return False
        current /= part
    return True


def validate_link(source: Path, raw_target: str, anchor_cache: dict[Path, set[str]]) -> str | None:
    target = unquote(raw_target)
    parsed = urlsplit(target)
    if parsed.scheme.lower() in EXTERNAL_SCHEMES or target.startswith("//"):
        return None

    path_parts = tuple(part for part in parsed.path.split("/") if part)
    link_base = ROOT if parsed.path.startswith("/") else source.parent
    destination = source if not path_parts else link_base.joinpath(*path_parts).resolve()
    try:
        destination.relative_to(ROOT)
    except ValueError:
        return f"link leaves repository: {raw_target}"

    if not destination.exists():
        return f"missing target: {raw_target}"
    if not has_exact_case(destination):
        return f"target casing differs from filesystem: {raw_target}"

    if parsed.fragment:
        if destination.is_dir():
            return f"anchor targets a directory: {raw_target}"
        expected = unquote(parsed.fragment).lower()
        if destination not in anchor_cache:
            try:
                anchor_cache[destination] = anchors(destination)
            except UnicodeDecodeError:
                return f"anchor targets a non-text file: {raw_target}"
        if expected not in anchor_cache[destination]:
            return f"missing anchor #{parsed.fragment}: {raw_target}"
    return None


def main() -> int:
    files = markdown_files()
    anchor_cache: dict[Path, set[str]] = {}
    failures: list[str] = []
    for source in files:
        text = source.read_text(encoding="utf-8")
        for target in link_targets(text):
            problem = validate_link(source, target, anchor_cache)
            if problem:
                failures.append(f"{source.relative_to(ROOT)}: {problem}")

    if failures:
        print("Markdown link validation failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(f"Validated {len(files)} Markdown files: local targets and anchors resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
