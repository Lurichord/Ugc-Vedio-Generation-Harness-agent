"""Intent-layer MCP server. Isolated from narrative / stage execution tools."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import BaseModel, ConfigDict, Field

from ..harness.stage_execution import run_repair_task
from ..intake.actions import (
    execute_start_project,
    get_description_element,
    inspect_progress,
    list_dependency_graph,
    list_description_outline,
    repair_description,
    start_project,
)
from ..intake.graph_ops import bind_shot, create_shot, remove_node
from ..intake.identity import create_identity, update_identity
from ..intake.models import IntakeSession
from ..intake.tools import (
    CONFIGURE_SESSION,
    DESCRIPTION_GET_ELEMENT,
    DESCRIPTION_LIST_OUTLINE,
    GRAPH_REMOVE,
    IDENTITY_CREATE,
    IDENTITY_UPDATE,
    HARNESS_INSPECT,
    HARNESS_LIST_GRAPH,
    HARNESS_REPAIR,
    HARNESS_START_PROJECT,
    INTAKE_MCP_TOOLS,
    SHOT_BIND,
    SHOT_CREATE,
)

try:
    from app.backend.graph_history import install_hooks as _install_graph_history

    _install_graph_history()
except ImportError:
    pass

CONFIGURE_TOOL = CONFIGURE_SESSION

_session_path: Path | None = None
_dry_run = False
_output_root: Path | None = None


class IdentityPatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(description="图上的 cast:/prop:/place:，或当前名字。")
    name: str | None = Field(default=None, description="新名字；不改则不填。")
    description: str | None = Field(default=None, description="新简介；不改则不填。")
    appearance: str | None = Field(default=None, description="新外观；不改则不填。")


mcp = MCPServer(
    "ugc-intake",
    version="1.0.0",
    instructions=(
        "Intent-layer tools only: read description or a slim graph, update "
        "identity, add/bind/remove graph nodes, then dispatch "
        "start/inspect/repair. Does not write "
        "working_brief and does not expose narrative.* tools."
    ),
)


def reset() -> None:
    global _session_path, _dry_run, _output_root
    _session_path = None
    _dry_run = False
    _output_root = None


def _load_session() -> IntakeSession:
    if _session_path is None:
        raise RuntimeError("intake.configure_session must be called first")
    return IntakeSession.model_validate_json(
        _session_path.read_text(encoding="utf-8")
    )


def _save_session(session: IntakeSession) -> None:
    if _session_path is None:
        raise RuntimeError("intake.configure_session must be called first")
    _session_path.parent.mkdir(parents=True, exist_ok=True)
    _session_path.write_text(session.model_dump_json(indent=2), encoding="utf-8")


def _patch_payloads(patches: list[IdentityPatchInput] | list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for item in patches or []:
        if isinstance(item, IdentityPatchInput):
            payloads.append(item.model_dump(exclude_none=True))
        else:
            payloads.append(dict(item))
    return payloads


@mcp.tool(
    name=CONFIGURE_SESSION,
    description="把本进程指到当前拍的 IntakeSession JSON。由 host 调用，模型不要选。",
    structured_output=True,
)
def configure_session(
    session_path: Annotated[str, Field(description="IntakeSession JSON 路径。")],
    dry_run: Annotated[bool, Field(description="预览开工和修复，不真正制作。")] = False,
    output_root: Annotated[str | None, Field(description="开工输出根目录。")] = None,
) -> dict[str, Any]:
    global _session_path, _dry_run, _output_root
    path = Path(session_path)
    if not path.is_file():
        raise FileNotFoundError(session_path)
    _session_path = path
    _dry_run = dry_run
    _output_root = Path(output_root) if output_root else None
    session = _load_session()
    return {
        "ok": True,
        "session_id": session.session_id,
        "tools": list(INTAKE_MCP_TOOLS),
    }


@mcp.tool(
    name=DESCRIPTION_LIST_OUTLINE,
    description="列出 structure 大纲：id 与标题/命题，不含正文。",
    structured_output=True,
)
def description_list_outline() -> dict[str, Any]:
    return list_description_outline(_load_session())


@mcp.tool(
    name=DESCRIPTION_GET_ELEMENT,
    description="读取一个 description 元素，ref 如 beat:b3。",
    structured_output=True,
)
def description_get_element(
    ref: Annotated[str, Field(description="元素 ref，如 beat:b3、shot:shot_a4。")],
) -> dict[str, Any]:
    return get_description_element(_load_session(), ref)


@mcp.tool(
    name=IDENTITY_UPDATE,
    description=(
        "按内容改已有身份节点的字段，id 不变。"
        "ref 可以是图上的 cast:/prop:/place:，也可以是当前 name（如 转化糖浆）。"
        "同名身份视为同一件东西，一起改。"
        "不要新建节点，不要改 shot.binds。"
        "换物用这个，加新人/新物用 identity.create。"
        "改完用返回的 changed_refs 做画面那一次 harness.repair；"
        "还要换声音时再圈 audio: 派给 voice。"
    ),
    structured_output=True,
)
def identity_update(
    patches: Annotated[
        list[IdentityPatchInput] | None,
        Field(description="要改的身份，至少一条。每项必须有 ref。"),
    ] = None,
) -> dict[str, Any]:
    session, result = update_identity(_load_session(), _patch_payloads(patches))
    _save_session(session)
    return result


@mcp.tool(
    name=IDENTITY_CREATE,
    description=(
        "新增一个身份根节点（cast/prop/place）。id 自动分配。"
        "要画进现有镜：再 shot.bind，然后 harness.repair。"
        "要新开一镜：再 shot.create，binds 带上返回的 ref。"
    ),
    structured_output=True,
)
def identity_create(
    kind: Annotated[
        Literal["cast", "prop", "place"],
        Field(description="身份种类：人物 cast、道具 prop、地点 place。"),
    ],
    name: Annotated[str, Field(description="显示名，图上同名视为同一件东西。")],
    description: Annotated[str, Field(description="这是谁/什么，干什么用。")],
    appearance: Annotated[str | None, Field(description="外观，出参考图时用。")] = None,
    role: Annotated[
        Literal["narrator", "performer"],
        Field(description="出镜角色。默认 performer。"),
    ] = "performer",
) -> dict[str, Any]:
    session, result = create_identity(
        _load_session(),
        kind=kind,
        name=name,
        description=description,
        appearance=appearance,
        role=role,
    )
    _save_session(session)
    return result


@mcp.tool(
    name=SHOT_CREATE,
    description=(
        "派 narrative task 让 generic 写一镜新 shot。"
        "只声明锚点、binds、插在哪一镜后和 instruction。"
        "不改已有镜的 binds。"
    ),
    structured_output=True,
)
def shot_create(
    anchor_ref: Annotated[
        str,
        Field(description="这镜挂在哪个结构节点上，如 scene:s2、step:st3。"),
    ],
    instruction: Annotated[str, Field(description="这一镜要拍什么。")],
    binds: Annotated[
        list[str] | None,
        Field(description="本镜绑定的身份 ref，如 cast:e1。"),
    ] = None,
    after_ref: Annotated[
        str | None,
        Field(description="插在哪一镜后面，如 shot:shot_a3；不填则接到末尾。"),
    ] = None,
) -> dict[str, Any]:
    session = _load_session()
    executor = None
    if not _dry_run and session.project_dir:
        project_dir = Path(session.project_dir)

        def executor(task, instruction_text: str) -> None:
            run_repair_task(project_dir, task, instruction_text)

    session, result = create_shot(
        session,
        anchor_ref=anchor_ref,
        binds=binds,
        after_ref=after_ref,
        instruction=instruction,
        dry_run=_dry_run,
        run_task=executor,
    )
    _save_session(session)
    return result


@mcp.tool(
    name=SHOT_BIND,
    description="把已有身份追加到现有镜的 binds，只加不换不删。改完用返回的 shot_refs 做 harness.repair 改画面。",
    structured_output=True,
)
def shot_bind(
    shot_refs: Annotated[
        list[str] | None,
        Field(description="要追加绑定的镜头，如 shot:shot_a4。"),
    ] = None,
    identity_ref: Annotated[
        str,
        Field(description="已有身份 ref 或当前名字。"),
    ] = "",
) -> dict[str, Any]:
    session, result = bind_shot(
        _load_session(),
        shot_refs=shot_refs,
        identity_ref=identity_ref,
    )
    _save_session(session)
    return result


@mcp.tool(
    name=GRAPH_REMOVE,
    description="删除身份或镜头节点。身份若还有 shot dependents 会失败。删镜会拆边并压缩 order。",
    structured_output=True,
)
def graph_remove(
    ref: Annotated[str, Field(description="要删的节点，如 prop:p1 或 shot:shot_a4。")],
) -> dict[str, Any]:
    session, result = remove_node(_load_session(), ref)
    _save_session(session)
    return result


@mcp.tool(
    name=HARNESS_INSPECT,
    description="从磁盘刷新 progress 和已落账的 intent。",
    structured_output=True,
)
def harness_inspect() -> dict[str, Any]:
    session, result = inspect_progress(_load_session())
    _save_session(session)
    return result


@mcp.tool(
    name=HARNESS_LIST_GRAPH,
    description=(
        "读取精简依赖图。无参返回内容根"
        "（cast/prop/place/beat/scene/step），身份根带 name、dependents、"
        "dependents_by_agent（narrative 下列 shot，voice 下列 audio）。"
        "around_ref 返回该节点、一跳邻居，以及下游两跳内的 shot/audio。不含 hash。"
    ),
    structured_output=True,
)
def harness_list_graph(
    around_ref: Annotated[
        str | None,
        Field(description="聚焦到这个节点、一跳邻居，以及下游两跳内的 shot/audio；不填则返回内容根。"),
    ] = None,
) -> dict[str, Any]:
    focus = (around_ref or "").strip() or None
    return list_dependency_graph(_load_session(), focus)


@mcp.tool(
    name=HARNESS_START_PROJECT,
    description="按当前 working_brief 开工。缺 topic 或 goal 会失败。",
    structured_output=True,
)
def harness_start_project() -> dict[str, Any]:
    starter = None
    if _output_root is not None and not _dry_run:
        root = _output_root
        starter = lambda brief: execute_start_project(brief, root)
    session, result = start_project(
        _load_session(),
        start_project=starter,
        dry_run=_dry_run,
    )
    _save_session(session)
    return result


@mcp.tool(
    name=HARNESS_REPAIR,
    description=(
        "把圈中节点交给一个制作 agent 按 instruction 微调。"
        "layer=spec 改规格：画面圈 shot 或身份，配音圈 audio。"
        "layer=asset 只重出参考图或成片；layer=voice 只重配。"
        "一次不要混 shot 和 audio。换身份文案时先 identity.update。"
    ),
    structured_output=True,
)
def harness_repair(
    target_refs: Annotated[
        list[str],
        Field(description="要修的节点。规格用身份/shot 或 audio；实现层同样用这些地址。"),
    ],
    instruction: Annotated[str, Field(description="这一轮要改成什么样。")],
    layer: Annotated[
        str,
        Field(description="spec 改叙事规格；asset 改参考图/成片；voice 改音色。默认 spec。"),
    ] = "spec",
) -> dict[str, Any]:
    session = _load_session()
    executor = None
    if not _dry_run and session.project_dir:
        project_dir = Path(session.project_dir)

        def executor(task, instruction_text: str) -> None:
            run_repair_task(project_dir, task, instruction_text)

    session, result = repair_description(
        session,
        target_refs,
        instruction,
        layer=layer,
        dry_run=_dry_run,
        run_task=executor,
    )
    _save_session(session)
    return result


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
