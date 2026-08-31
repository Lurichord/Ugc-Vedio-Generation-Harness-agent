"""Intake tool names and host-local skill activate / brief update. MCP owns the rest."""

from __future__ import annotations

from typing import Any

from .skills import list_skills, load_skill


DESCRIPTION_LIST_OUTLINE = "description.list_outline"
DESCRIPTION_GET_ELEMENT = "description.get_element"
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
    HARNESS_START_PROJECT,
    HARNESS_INSPECT,
    HARNESS_LIST_GRAPH,
    HARNESS_REPAIR,
)


def mcp_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": DESCRIPTION_LIST_OUTLINE,
            "description": "列出 structure 大纲：id 与标题/命题，不含正文。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": DESCRIPTION_GET_ELEMENT,
            "description": "读取一个 description 元素，ref 如 beat:b3。",
            "parameters": {
                "type": "object",
                "properties": {"ref": {"type": "string"}},
                "required": ["ref"],
                "additionalProperties": False,
            },
        },
        {
            "name": HARNESS_START_PROJECT,
            "description": "按当前 working_brief 开工。缺 topic 或 goal 会失败。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": HARNESS_INSPECT,
            "description": "从磁盘刷新 progress 和已落账的 intent。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": HARNESS_LIST_GRAPH,
            "description": (
                "读取精简依赖图。无参只返回内容根节点"
                "（cast/prop/place/beat/scene/step）；"
                "around_ref 返回该节点及一跳邻居。不含 hash。"
            ),
            "parameters": {
                "type": "object",
                "properties": {"around_ref": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        {
            "name": HARNESS_REPAIR,
            "description": "弄脏 target_refs 及其下游，并拉起制作 agent 按 instruction 修复。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "instruction": {"type": "string"},
                },
                "required": ["target_refs", "instruction"],
                "additionalProperties": False,
            },
        },
    ]


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
