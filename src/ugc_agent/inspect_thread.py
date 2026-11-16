from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from .api.views import project_run

DEFAULT_DB = Path("var/checkpoints.sqlite")


def list_thread_ids(db: Path) -> list[str]:
    connection = sqlite3.connect(db)
    try:
        rows = connection.execute(
            "SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id"
        ).fetchall()
    finally:
        connection.close()
    return [row[0] for row in rows]


def load_thread(db: Path, thread_id: str) -> dict[str, Any]:
    connection = sqlite3.connect(db, check_same_thread=False)
    saver = SqliteSaver(connection)
    saver.setup()
    try:
        packed = saver.get_tuple({"configurable": {"thread_id": thread_id}})
        if packed is None:
            raise KeyError(thread_id)
        values = packed.checkpoint.get("channel_values") or {}
        messages = list(values.get("messages") or [])
        return {
            "thread_id": thread_id,
            "checkpoint_id": packed.config["configurable"].get("checkpoint_id"),
            "parent_checkpoint_id": (packed.parent_config or {})
            .get("configurable", {})
            .get("checkpoint_id"),
            "metadata": packed.metadata,
            "messages": [_message(message) for message in messages],
            "turns": project_run(messages),
        }
    finally:
        connection.close()


def _message(message: Any) -> dict[str, Any]:
    text = message.content if isinstance(getattr(message, "content", None), str) else ""
    if len(text) > 2000:
        text = text[:2000] + "…"
    row: dict[str, Any] = {"type": getattr(message, "type", type(message).__name__)}
    name = getattr(message, "name", None)
    if name:
        row["name"] = name
    if text:
        row["content"] = text
    calls = getattr(message, "tool_calls", None) or []
    if calls:
        row["tool_calls"] = [
            {
                "name": call.get("name", "") if isinstance(call, dict) else getattr(call, "name", ""),
                "args": call.get("args", {}) if isinstance(call, dict) else getattr(call, "args", {}),
            }
            for call in calls
        ]
    status = getattr(message, "status", None)
    if status:
        row["status"] = status
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="查看指定 thread 的 Checkpointer 内容")
    parser.add_argument("thread_id", nargs="?", help="项目 id / thread_id")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="checkpoints.sqlite 路径")
    parser.add_argument("--list", action="store_true", help="只列出库里的 thread_id")
    args = parser.parse_args(argv)
    if not args.db.is_file():
        print(f"找不到数据库: {args.db}", file=sys.stderr)
        return 1
    if args.list or not args.thread_id:
        for thread_id in list_thread_ids(args.db):
            print(thread_id)
        return 0
    try:
        payload = load_thread(args.db, args.thread_id)
    except KeyError:
        print(f"没有这个 thread: {args.thread_id}", file=sys.stderr)
        return 1
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
