"""Intake tool names and host-local skill activate / brief update. MCP owns the rest."""

from __future__ import annotations

from typing import Any

from .skills import list_skills, load_skill


DESCRIPTION_LIST_OUTLINE = "description.list_outline"
DESCRIPTION_GET_ELEMENT = "description.get_element"
IDENTITY_UPDATE = "identity.update"
IDENTITY_CREATE = "identity.create"
SHOT_CREATE = "shot.create"
SHOT_BIND = "shot.bind"
GRAPH_REMOVE = "graph.remove"
HARNESS_START_PROJECT = "harness.start_project"
HARNESS_INSPECT = "harness.inspect"
HARNESS_LIST_GRAPH = "harness.list_graph"
HARNESS_REPAIR = "harness.repair"
SKILL_ACTIVATE = "skill.activate"
BRIEF_UPDATE = "brief.update"
CONFIGURE_SESSION = "intake.configure_session"

INTAKE_MCP_TOOLS = (
    DESCRIPTION_LIST_OUTLINE,
    DESCRIPTION_GET_ELEMENT,
    IDENTITY_UPDATE,
    IDENTITY_CREATE,
    SHOT_CREATE,
    SHOT_BIND,
    GRAPH_REMOVE,
    HARNESS_START_PROJECT,
    HARNESS_INSPECT,
    HARNESS_LIST_GRAPH,
    HARNESS_REPAIR,
)


def mcp_tool_specs() -> list[dict[str, Any]]:
    from ..mcp_servers.intake import mcp as intake_server
    from ..tools.mcp import mcp_tool_catalog

    return mcp_tool_catalog(intake_server, names=INTAKE_MCP_TOOLS)


def host_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": SKILL_ACTIVATE,
            "description": (
                "读入一篇 skill 做法说明书。激活不等于办事。"
                "名称只能包含小写字母、数字和连字符。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$",
                        "description": "技能名称只能包含小写字母、数字和连字符",
                    }
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        {
            "name": BRIEF_UPDATE,
            "description": (
                "部分更新 working_brief。只填要改的字段，没改的键不要带。"
                "点头确认、选择题、改口时用。不等于开工。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "production_mode": {
                        "type": "string",
                        "enum": ["auto", "explainer", "drama", "tutorial"],
                    },
                    "video_profile": {"type": "string"},
                    "target": {
                        "type": "object",
                        "properties": {
                            "platform": {"type": "string"},
                            "duration_target_ms": {"type": "integer"},
                            "aspect_ratio": {"type": "string"},
                            "language": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                    "audience": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "knowledge_level": {
                                "type": "string",
                                "enum": ["beginner", "general", "expert"],
                            },
                        },
                        "additionalProperties": False,
                    },
                    "communication": {
                        "type": "object",
                        "properties": {
                            "goal": {"type": "string"},
                            "tone": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "creator_persona": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
                "additionalProperties": False,
            },
        },
    ]


def activate_skill(name: str) -> dict[str, Any]:
    cleaned = name.strip()
    if not cleaned:
        return {"ok": False, "error": "skill 名不能为空"}
    try:
        body = load_skill(cleaned)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except FileNotFoundError:
        known = ", ".join(item["name"] for item in list_skills()) or "无"
        return {"ok": False, "error": f"没有 skill {cleaned}。目录：{known}"}
    return {"ok": True, "name": cleaned, "skill": body}
