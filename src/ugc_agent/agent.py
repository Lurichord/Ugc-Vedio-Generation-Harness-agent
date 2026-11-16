from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
    ToolErrorMiddleware,
)
from langchain.chat_models import BaseChatModel
from langchain.messages import HumanMessage
from langchain.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .context import AgentContext
from .skills import SkillsMiddleware
from .step_pause import StepPauseMiddleware
from .tools import AGENT_TOOLS


def system_prompt() -> str:
    provider = (os.environ.get("UGC_VIDEO_PROVIDER") or "seedance").strip().lower()
    if provider == "minimax":
        skill, tool, other_skill, other_tool, vendor = (
            "h3-prompt",
            "video-gen-minimax",
            "seedance-prompt",
            "video-gen",
            "MiniMax H3",
        )
    else:
        skill, tool, other_skill, other_tool, vendor = (
            "seedance-prompt",
            "video-gen",
            "h3-prompt",
            "video-gen-minimax",
            "Seedance",
        )
    return f"""你是短视频制作搭档，用中文和用户把片子做出来。用户听到的是在聊片子的人，不是系统、问卷或后台组件。不要自称 agent / system，不要把字段名念出来（例如 video_type、owner_ref）。

## 职责
1. 先弄清要做什么片子，再按对应 composition skill 推进。
2. 中间事实写入项目状态：大纲、故事、分镜、媒体、成片。
3. 每个 Turn 结束时必须有一句给用户听的中文。空内容、只调工具都不算回复。

## 选片种
四种：数字人讲解、演绎、广告、科普。用户没说清时先问这一件，问清前禁止 save_outline，也禁止开始生成。
一次只问一件缺的事。不要甩字段清单，不要念 ad / drama / explainer / avatar-explainer。
用户已经说清片种就直接 load_skill，不要再确认一遍。

## Skill
下面目录只有名字和一句描述。写大纲前必须先 load_skill 加载对应 composition 全文。
制作过程必须严格按已选定的那份 composition skill 逐步走，禁止跳步，禁止自己另排流程。下一步该 save_shot_design 就先写分镜，不要先 image-gen / record_media。
写分镜 prompt 或调用 {tool} 前，再 load_skill {skill}。
对当前该做哪一步不清楚、记不清前置条件、或工具报错像 shot design is missing：立刻再 load_skill 加载同一条 composition 全文，对照步骤继续，不要靠记忆硬做。
不要一次加载多条 composition。foundation 的 {skill} 可以和当前片种一起用。片种做法以 composition 全文为准，画面正文写法以 {skill} 为准。

## 视频厂商
当前视频厂商是 {vendor}。写 prompt 用 {skill}，出片调用 {tool}。不要 load_skill {other_skill}，不要调用 {other_tool}。

## 账本
先 read_project。
内容用 save_outline / save_story / save_shot_design 整层替换。
生成或用户给的素材用 record_media 挂上；成片用 set_deliverable。
不要另存中间 json 文件。生成工具只返回路径，你必须自己 record_media。

## 对用户
每个 Turn 最后一条助手消息必须是有内容的中文口语：做了什么、卡在哪、或下一步要用户看什么。禁止空回复。调完工具也要说人话，工具调用本身不是对用户的回复。
工具失败或看不懂输入时，收成一句人话，请用户再说清楚，不要编选题。
闲聊、道谢、说先这样：用人话接，再轻轻拉回还缺的那一件。
用户改口换主题或片种：先 read_project，再按新方向走，不要沿用旧大纲硬编。

## 边缘
素材不够就说缺什么，不要假装已经生成。
当前 Turn 只推进能确定的一步；前置步骤还没写入项目状态，就不要跳到后面。
"""


SYSTEM_PROMPT = system_prompt()


def on_tool_error(exc: Exception, request) -> str:
    name = request.tool_call["name"]
    return (
        f"`{name}` 失败：{type(exc).__name__}: {exc}。"
        "根据错误改参数再试，或用一句话告诉用户哪里卡住了。"
    )


def _video_gen_review(tool_call, state, runtime) -> str:
    args = tool_call["args"]
    return (
        f"生成镜头视频\n"
        f"mode: {args.get('mode')}\n"
        f"时长: {args.get('duration_seconds')}\n"
        f"prompt:\n{args.get('prompt')}"
    )


def build_agent(
    model: BaseChatModel,
    *,
    tools: Sequence[BaseTool] = AGENT_TOOLS,
    checkpointer: Any | None = None,
    max_steps: int = 32,
):
    video_interrupt = {
        "allowed_decisions": ["approve", "edit", "reject"],
        "description": _video_gen_review,
    }
    return create_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt(),
        middleware=[
            SkillsMiddleware(),
            StepPauseMiddleware(),
            HumanInTheLoopMiddleware(
                interrupt_on={
                    "video-gen": video_interrupt,
                    "video-gen-minimax": video_interrupt,
                },
                interrupt_mode="per_call",
            ),
            ToolErrorMiddleware(on_tool_error),
            ModelCallLimitMiddleware(run_limit=max_steps, exit_behavior="error"),
        ],
        context_schema=AgentContext,
        checkpointer=checkpointer or InMemorySaver(),
    )


def _run_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def invoke_turn(
    agent,
    message: str,
    *,
    thread_id: str,
    context: AgentContext,
):
    return agent.invoke(
        {
            "messages": [
                HumanMessage(
                    content=message,
                    additional_kwargs={"turn_id": context.turn_id},
                )
            ]
        },
        config=_run_config(thread_id),
        context=context,
    )


def resume_turn(
    agent,
    decisions: dict,
    *,
    thread_id: str,
    context: AgentContext,
):
    return agent.invoke(
        Command(resume=decisions),
        config=_run_config(thread_id),
        context=context,
    )


def make_sqlite_checkpointer(path: Path) -> tuple[sqlite3.Connection, SqliteSaver]:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    saver = SqliteSaver(connection)
    saver.setup()
    return connection, saver


@contextmanager
def sqlite_checkpointer(path: Path) -> Iterator[SqliteSaver]:
    connection, saver = make_sqlite_checkpointer(path)
    try:
        yield saver
    finally:
        connection.close()
