"""Interactive host for the intent agent."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import uuid4

from ..shared.llm import StructuredLLM
from ..shared.settings import LLMSettings
from .agent import IntentAgent
from .brief_sync import ChainedBriefSync, HeuristicBriefSync, LlmBriefSync
from .host import OPENING_REPLY, IntentHost, new_session
from .mcp_runtime import IntakeMcpRuntime
from .models import Inbound, IntakeSession

_QUIT = {"q", "quit", "exit", "退出"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ugc-intake",
        description="意图解析层：保存 brief/对白，由 agent 决定问你还是经 MCP 派制作。",
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--api-keys-file", type=Path)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("outputs"),
        help="开工后的项目输出根目录（默认 outputs）",
    )
    parser.add_argument(
        "--session-file",
        type=Path,
        help="读写 IntakeSession JSON；不传则写到输出目录下的临时文件",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="start_project 只收成 CreativeBrief，不真正启动 narrative",
    )
    return parser


def format_status(session: IntakeSession) -> str:
    brief = session.working_brief
    topic = brief.topic or "无"
    return f"[{session.status}] topic={topic}"


def format_reply(session: IntakeSession, message: str) -> str:
    brief = session.working_brief
    fields: list[str] = []
    if brief.topic:
        fields.append(f"topic={brief.topic}")
    if brief.production_mode:
        fields.append(f"mode={brief.production_mode}")
    if brief.target is not None:
        fields.append(f"duration={brief.target.duration_target_ms // 1000}s")
    if brief.communication is not None and brief.communication.goal:
        fields.append(f"goal={brief.communication.goal}")
    return (
        f"[fields] {'  '.join(fields) or '（尚未填写）'}\n"
        f"意图：{message}"
    )


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        settings = LLMSettings.from_environment(args.api_keys_file, args.model)
        llm = StructuredLLM(settings)
        session_path = args.session_file or (
            args.output_root / ".intake" / f"session_{uuid4().hex}.json"
        )
        session = _load_session(session_path)
        host = IntentHost(
            IntentAgent(llm),
            IntakeMcpRuntime(
                session_path,
                dry_run=args.dry_run,
                output_root=None if args.dry_run else args.output_root,
            ),
            brief_sync=ChainedBriefSync(HeuristicBriefSync(), LlmBriefSync(llm)),
        )
    except Exception as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(session.last_message or OPENING_REPLY)
    print(format_status(session))
    print("输入内容回车即可；exit / quit / 退出 结束。", flush=True)

    while True:
        try:
            text = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in _QUIT or text in _QUIT:
            break
        try:
            result = host.respond(session, Inbound(text=text))
        except Exception as exc:
            print(f"本轮失败：{exc}", file=sys.stderr)
            continue
        session = result.session
        _save_session(session_path, session)
        if session.project_dir:
            print(f"harness：项目目录 {session.project_dir}")
        print(format_reply(session, result.message))
        print(format_status(session))
        if result.done:
            break


def _load_session(path: Path) -> IntakeSession:
    if not path.is_file():
        return new_session()
    return IntakeSession.model_validate_json(path.read_text(encoding="utf-8"))


def _save_session(path: Path, session: IntakeSession) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(session.model_dump_json(indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
