"""GenericAgent skill catalog. Activate injects markdown; it does not do work.

Each agent package owns ``skills/*.md``. Task agent names that do not match
the package folder are mapped in ``_AGENT_SKILL_PACKAGES``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SKILL_ACTIVATE = "skill.activate"
AGENTS_ROOT = Path(__file__).parent
SKILLS_ROOT = AGENTS_ROOT
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKILL_NAME_ERROR = "技能名称只能包含小写字母、数字和连字符"

_AGENT_SKILL_PACKAGES = {
    "narrative_agent": "narrative_agent",
    "asset_stage": "asset_agent",
    "asset_agent": "asset_agent",
    "voice_agent": "voice_agent",
    "editorial_agent": "editorial_agent",
    "timeline_agent": "timeline_agent",
    "render_agent": "render_agent",
}


def validate_skill_name(name: str) -> str:
    cleaned = name.strip()
    if not SKILL_NAME_RE.fullmatch(cleaned):
        raise ValueError(SKILL_NAME_ERROR)
    return cleaned


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


def skill_dir_for(agent: str) -> Path:
    package = _AGENT_SKILL_PACKAGES.get(agent, agent)
    return AGENTS_ROOT / package / "skills"


def _skill_dirs(agent: str | None, root: Path | None) -> list[Path]:
    if root is not None:
        return [root]
    if agent:
        return [skill_dir_for(agent)]
    seen: set[Path] = set()
    dirs: list[Path] = []
    for package in _AGENT_SKILL_PACKAGES.values():
        path = AGENTS_ROOT / package / "skills"
        if path in seen:
            continue
        seen.add(path)
        dirs.append(path)
    return dirs


def _catalog_entry(path: Path) -> dict[str, str] | None:
    try:
        name = validate_skill_name(path.stem)
    except ValueError:
        return None
    meta, _ = parse_skill(path.read_text(encoding="utf-8"))
    declared = meta.get("name")
    if declared and declared != name:
        return None
    return {
        "name": name,
        "description": meta.get("description") or "",
        "agent": meta.get("agent") or "",
    }


def list_agent_skills(agent: str | None = None, root: Path | None = None) -> list[dict[str, str]]:
    catalog: list[dict[str, str]] = []
    seen: set[str] = set()
    for base in _skill_dirs(agent, root):
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.md")):
            item = _catalog_entry(path)
            if item is None:
                continue
            declared_agent = item["agent"]
            if agent and declared_agent and declared_agent != agent:
                continue
            key = f"{item['agent']}:{item['name']}"
            if key in seen:
                continue
            seen.add(key)
            catalog.append(item)
    return catalog


def load_agent_skill(name: str, *, agent: str, root: Path | None = None) -> str:
    cleaned = validate_skill_name(name)
    for base in _skill_dirs(agent, root):
        path = base / f"{cleaned}.md"
        if not path.is_file():
            continue
        meta, body = parse_skill(path.read_text(encoding="utf-8"))
        declared_agent = meta.get("agent")
        if declared_agent and declared_agent != agent:
            continue
        return body
    raise FileNotFoundError(cleaned)


def activate_agent_skill(
    agent: str,
    name: str,
    root: Path | None = None,
) -> dict[str, Any]:
    cleaned = name.strip()
    if not cleaned:
        return {"ok": False, "error": "skill 名不能为空"}
    try:
        body = load_agent_skill(cleaned, agent=agent, root=root)
    except FileNotFoundError:
        known = ", ".join(item["name"] for item in list_agent_skills(agent, root)) or "无"
        return {"ok": False, "error": f"没有 skill {cleaned}。目录：{known}"}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "name": cleaned, "skill": body}


def skill_activate_spec(agent: str) -> dict[str, Any]:
    catalog = list_agent_skills(agent)
    if catalog:
        listing = "；".join(
            f"{item['name']}（{item['description']}）" if item["description"] else item["name"]
            for item in catalog
        )
        description = f"读入一篇 skill 做法说明书。激活不等于办事。本阶段目录：{listing}"
    else:
        description = "读入一篇 skill 做法说明书。当前阶段没有可激活的 skill。"
    return {
        "name": SKILL_ACTIVATE,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    }
