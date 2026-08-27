"""Skill catalog on disk. Activate injects markdown; it does not do work."""

from __future__ import annotations

import re
from pathlib import Path


SKILLS_ROOT = Path(__file__).parent / "skills"
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKILL_NAME_ERROR = "技能名称只能包含小写字母、数字和连字符"


def validate_skill_name(name: str) -> str:
    cleaned = name.strip()
    if not SKILL_NAME_RE.fullmatch(cleaned):
        raise ValueError(SKILL_NAME_ERROR)
    return cleaned


def list_skills(root: Path | None = None) -> list[dict[str, str]]:
    base = root or SKILLS_ROOT
    if not base.is_dir():
        return []
    catalog: list[dict[str, str]] = []
    for path in sorted(base.glob("*.md")):
        try:
            name = validate_skill_name(path.stem)
        except ValueError:
            continue
        meta, _ = parse_skill(path.read_text(encoding="utf-8"))
        declared = meta.get("name")
        if declared and declared != name:
            continue
        catalog.append(
            {
                "name": name,
                "description": meta.get("description") or "",
            }
        )
    return catalog


def load_skill(name: str, root: Path | None = None) -> str:
    cleaned = validate_skill_name(name)
    base = root or SKILLS_ROOT
    path = base / f"{cleaned}.md"
    if not path.is_file():
        raise FileNotFoundError(cleaned)
    _, body = parse_skill(path.read_text(encoding="utf-8"))
    return body


def parse_skill(text: str) -> tuple[dict[str, str], str]:
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return {}, text.strip()
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return {}, text.strip()
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    return meta, parts[2].strip()
