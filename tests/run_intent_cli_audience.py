"""Drive the real ugc-intake CLI as a first-time viewer. Do not script the agent."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from ugc_harness.agents.narrative_agent.models import NarrativeArtifact
from ugc_harness.harness.repair import load_project_state
from ugc_harness.intake.models import IntakeSession

ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "test" / "intent_cli_audience"
PROMPT = "你："
START_MARK = "harness：项目目录"

PERSONAS: list[dict[str, str]] = [
    {
        "slug": "01_coffee",
        "opening": "我想做个视频",
        "topic": "手冲咖啡",
        "goal": "让新手分清粉水比",
        "platform": "抖音",
        "duration": "一分半",
        "audience": "完全没冲过的人",
        "revision": "第三段太长，收成两句，粉水比那句留下",
    },
    {
        "slug": "02_tomato_egg",
        "opening": "帮我做条抖音，我也不会拍",
        "topic": "番茄炒蛋",
        "goal": "让宿舍党今晚敢下锅",
        "platform": "抖音",
        "duration": "一分半",
        "audience": "只会煮面的学生",
        "revision": "开场钩子太软，改成先问会不会把蛋炒老",
    },
    {
        "slug": "03_bike_tube",
        "opening": "我想做个教程，但不知道从哪说起",
        "topic": "换自行车内胎",
        "goal": "看完能自己把后轮内胎换上",
        "platform": "抖音",
        "duration": "一分钟",
        "audience": "第一次修车的人",
        "revision": "第三段别讲原理了，直接说撬胎步骤",
    },
    {
        "slug": "04_intern",
        "opening": "想拍个短剧，我没做过",
        "topic": "实习生第一天加班",
        "goal": "让观众觉得被看见",
        "platform": "抖音",
        "duration": "一分半",
        "audience": "刚工作的年轻人",
        "revision": "第三段台词太满，收短，加班那句留下",
    },
    {
        "slug": "05_earbuds",
        "opening": "就抖音一分半那种，行吗",
        "topic": "通勤耳机怎么选",
        "goal": "能按预算选出一款，不要被参数吓到",
        "platform": "抖音",
        "duration": "一分半",
        "audience": "通勤上班的人",
        "revision": "第三段参数堆太多，留两个就够",
    },
    {
        "slug": "06_rent",
        "opening": "先做理财吧……算了好像也不对",
        "topic": "租房避坑",
        "goal": "能认出中介合同里的坑",
        "platform": "抖音",
        "duration": "一分半",
        "audience": "第一次在外面租房的人",
        "revision": "第三段太吓人，改成先给三条检查清单",
    },
    {
        "slug": "07_knee",
        "opening": "做个视频吧，我也不知道该怎么讲",
        "topic": "跑步伤膝盖",
        "goal": "知道什么时候该停",
        "platform": "抖音",
        "duration": "一分半",
        "audience": "刚开始跑步的人",
        "revision": "第三段别吓人，改成先说热身",
    },
    {
        "slug": "08_interview",
        "opening": "我想做自我介绍相关的",
        "topic": "面试自我介绍",
        "goal": "让应届生开口第一句不慌",
        "platform": "抖音",
        "duration": "一分半",
        "audience": "应届生",
        "revision": "开场这段不对，改短一点，钩子问题留下",
    },
    {
        "slug": "09_fridge",
        "opening": "随便做一条日常的？",
        "topic": "冰箱收纳",
        "goal": "周末能清出一层隔板",
        "platform": "抖音",
        "duration": "一分钟",
        "audience": "懒得收拾但冰箱很乱的人",
        "revision": "第三段那句太凶，改成先拿出过期的",
    },
    {
        "slug": "10_plants",
        "opening": "我绿植老养死，想做视频",
        "topic": "家里绿植总养死",
        "goal": "能判断是水多了还是光不够",
        "platform": "抖音",
        "duration": "一分半",
        "audience": "养过几次都养死的人",
        "revision": "第三段诊断太绕，改成先看叶子再看土",
    },
]


def audience_reply(
    persona: dict[str, str],
    agent_text: str,
    session: IntakeSession | None,
    *,
    sent_revision: bool,
    user_turns: int,
) -> str:
    started = bool(session and session.project_dir)
    if started and sent_revision:
        return "退出"
    if started:
        return persona["revision"]

    question = agent_text or ""
    if any(token in question for token in ("主题", "讲什么", "做什么视频", "拍什么")):
        return f"做一条讲{persona['topic']}的{persona['platform']}"
    if any(token in question for token in ("目标", "看完", "希望观众", "明白什么")):
        return f"目标是{persona['goal']}。"
    if any(token in question for token in ("平台", "多长", "多久", "时长", "几分钟")):
        return f"{persona['platform']}，大概{persona['duration']}。"
    if any(token in question for token in ("给谁", "观众", "谁看")):
        return f"给{persona['audience']}看。"
    if user_turns >= 6:
        return (
            f"主题是{persona['topic']}。"
            f"目标是{persona['goal']}。"
            f"{persona['platform']}，{persona['duration']}。"
            "就按现在的开做。"
        )
    return f"就{persona['topic']}吧，我也不大会说。"


def snapshot_project(project_dir: Path | None) -> dict[str, Any]:
    if project_dir is None or not project_dir.is_dir():
        return {}
    payload: dict[str, Any] = {"project_dir": str(project_dir)}
    artifact_path = project_dir / "narrative_artifact.json"
    if artifact_path.is_file():
        artifact = NarrativeArtifact.model_validate_json(
            artifact_path.read_text(encoding="utf-8")
        )
        beats = getattr(artifact.planning, "beats", None) or []
        segments = getattr(artifact.script, "segments", None) or []
        payload["brief"] = {
            "topic": artifact.brief.topic,
            "goal": (
                artifact.brief.communication.goal
                if artifact.brief.communication
                else None
            ),
        }
        payload["beats"] = [
            {
                "id": beat.planned_beat_id,
                "goal": beat.semantic_goal,
            }
            for beat in beats
        ]
        payload["script"] = [
            {
                "id": segment.planned_beat_id,
                "text": segment.text,
            }
            for segment in segments
        ]
    state_path = project_dir / "harness" / "project_state.json"
    if state_path.is_file():
        state = load_project_state(project_dir)
        payload["graph"] = {
            ref: {
                "status": node.status,
                "locked": node.locked,
                "produced_by": node.produced_by,
            }
            for ref, node in state.dependency_graph.nodes.items()
        }
        production = state.production.structure
        prod_beats = getattr(production, "beats", None) or []
        payload["production_beats"] = [
            {
                "id": beat.planned_beat_id,
                "goal": beat.semantic_goal,
            }
            for beat in prod_beats
        ]
    return payload


def diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> str:
    if not before or not after:
        return "没有完整的修改前/后快照。\n"
    lines = ["# 修改前后差别", ""]
    before_script = {item["id"]: item["text"] for item in before.get("script", [])}
    after_script = {item["id"]: item["text"] for item in after.get("script", [])}
    changed_script = [
        key
        for key in sorted(set(before_script) | set(after_script))
        if before_script.get(key) != after_script.get(key)
    ]
    lines.append(f"剧本有变化的 beat：{', '.join(changed_script) or '无'}")
    lines.append("")
    for key in changed_script:
        lines.append(f"## {key}")
        lines.append("改前：")
        lines.append(before_script.get(key) or "（无）")
        lines.append("")
        lines.append("改后：")
        lines.append(after_script.get(key) or "（无）")
        lines.append("")

    before_prod = {
        item["id"]: item["goal"] for item in before.get("production_beats", [])
    }
    after_prod = {
        item["id"]: item["goal"] for item in after.get("production_beats", [])
    }
    changed_prod = [
        key
        for key in sorted(set(before_prod) | set(after_prod))
        if before_prod.get(key) != after_prod.get(key)
    ]
    if changed_prod:
        lines.append("## production.structure.beats")
        for key in changed_prod:
            lines.append(f"- `{key}`")
            lines.append(f"  前：{before_prod.get(key)}")
            lines.append(f"  后：{after_prod.get(key)}")
        lines.append("")

    before_graph = before.get("graph") or {}
    after_graph = after.get("graph") or {}
    status_changed = [
        ref
        for ref in sorted(set(before_graph) | set(after_graph))
        if (before_graph.get(ref) or {}).get("status")
        != (after_graph.get(ref) or {}).get("status")
    ]
    lines.append(
        f"图节点 status 有变化：{', '.join(status_changed) or '无'}"
    )
    for ref in status_changed:
        lines.append(
            f"- `{ref}` "
            f"{(before_graph.get(ref) or {}).get('status')} → "
            f"{(after_graph.get(ref) or {}).get('status')}"
        )
    lines.append("")
    return "\n".join(lines)


def _read_session(path: Path) -> IntakeSession | None:
    if not path.is_file():
        return None
    try:
        return IntakeSession.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _latest_agent_text(buffer: str) -> str:
    marker = "意图："
    if marker not in buffer:
        return buffer
    return buffer.rsplit(marker, 1)[-1].strip()


class CliAudience:
    def __init__(self, round_dir: Path, persona: dict[str, str]) -> None:
        self.round_dir = round_dir
        self.persona = persona
        self.session_path = round_dir / "session.json"
        self.output_root = round_dir / "project"
        self.tool_log = round_dir / "tools.jsonl"
        self.stdout_chunks: list[str] = []
        self.stderr_chunks: list[str] = []
        self._stderr_thread: threading.Thread | None = None

    def run(self, *, turn_timeout: int) -> dict[str, Any]:
        self.round_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["INTAKE_TOOL_LOG"] = str(self.tool_log)
        command = [
            sys.executable,
            "-m",
            "ugc_harness.intake",
            "--output-root",
            str(self.output_root),
            "--session-file",
            str(self.session_path),
        ]
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=0,
            env=env,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        self._stderr_thread = threading.Thread(
            target=self._pump_stderr,
            args=(process.stderr,),
            daemon=True,
        )
        self._stderr_thread.start()

        transcript: list[dict[str, str]] = []
        sent_revision = False
        started_at: str | None = None
        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        try:
            opening = self._wait_prompt(process, turn_timeout)
            transcript.append({"role": "agent", "content": opening})
            next_line = self.persona["opening"]
            for turn in range(1, 13):
                if process.poll() is not None:
                    break
                transcript.append({"role": "user", "content": next_line})
                if next_line == self.persona["revision"]:
                    sent_revision = True
                    session = _read_session(self.session_path)
                    before = snapshot_project(
                        Path(session.project_dir) if session and session.project_dir else None
                    )
                if next_line == "退出":
                    process.stdin.write(next_line + "\n")
                    process.stdin.flush()
                    break
                process.stdin.write(next_line + "\n")
                process.stdin.flush()
                reply = self._wait_prompt(process, turn_timeout)
                transcript.append({"role": "agent", "content": reply})
                session = _read_session(self.session_path)
                if session and session.project_dir and started_at is None:
                    started_at = session.project_dir
                    if not before:
                        before = snapshot_project(Path(session.project_dir))
                if sent_revision and session and session.project_dir:
                    after = snapshot_project(Path(session.project_dir))
                next_line = audience_reply(
                    self.persona,
                    _latest_agent_text(reply),
                    session,
                    sent_revision=sent_revision,
                    user_turns=turn,
                )
        finally:
            if process.poll() is None:
                try:
                    process.stdin.write("退出\n")
                    process.stdin.flush()
                except Exception:
                    pass
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
            leftover = process.stdout.read() if process.stdout else ""
            if leftover:
                self.stdout_chunks.append(leftover)

        session = _read_session(self.session_path)
        if session and session.project_dir and not after:
            after = snapshot_project(Path(session.project_dir))
        self._write_dump(transcript, before, after, session, process.returncode)
        return {
            "slug": self.persona["slug"],
            "started": bool(session and session.project_dir),
            "repaired": sent_revision,
            "project_dir": session.project_dir if session else None,
            "turns": len([item for item in transcript if item["role"] == "user"]),
            "returncode": process.returncode,
        }

    def _pump_stderr(self, stream) -> None:
        for line in stream:
            self.stderr_chunks.append(line)

    def _wait_prompt(self, process: subprocess.Popen[str], timeout: int) -> str:
        deadline = time.time() + timeout
        collected = ""
        assert process.stdout is not None
        while time.time() < deadline:
            if process.poll() is not None:
                rest = process.stdout.read() or ""
                collected += rest
                self.stdout_chunks.append(rest)
                return collected
            ch = process.stdout.read(1)
            if not ch:
                time.sleep(0.05)
                continue
            collected += ch
            if collected.endswith(PROMPT):
                self.stdout_chunks.append(collected)
                return collected[: -len(PROMPT)]
        self.stdout_chunks.append(collected)
        raise TimeoutError(f"{self.persona['slug']} 等待 CLI 提示超时")

    def _write_dump(
        self,
        transcript: list[dict[str, str]],
        before: dict[str, Any],
        after: dict[str, Any],
        session: IntakeSession | None,
        returncode: int | None,
    ) -> None:
        (self.round_dir / "stdout.log").write_text(
            "".join(self.stdout_chunks), encoding="utf-8"
        )
        (self.round_dir / "stderr.log").write_text(
            "".join(self.stderr_chunks), encoding="utf-8"
        )
        (self.round_dir / "transcript.json").write_text(
            json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tools = []
        if self.tool_log.is_file():
            for line in self.tool_log.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    tools.append(json.loads(line))
        (self.round_dir / "tools.json").write_text(
            json.dumps(tools, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (self.round_dir / "before.json").write_text(
            json.dumps(before, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (self.round_dir / "after.json").write_text(
            json.dumps(after, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (self.round_dir / "diff.md").write_text(
            diff_snapshots(before, after), encoding="utf-8"
        )
        (self.round_dir / "transcript.md").write_text(
            _transcript_markdown(self.persona, transcript, tools),
            encoding="utf-8",
        )
        project_dir = Path(session.project_dir) if session and session.project_dir else None
        if project_dir and (project_dir / "narrative_artifact.json").is_file():
            shutil.copy2(
                project_dir / "narrative_artifact.json",
                self.round_dir / "narrative_artifact.json",
            )
        summary = {
            "slug": self.persona["slug"],
            "opening": self.persona["opening"],
            "revision": self.persona["revision"],
            "project_dir": session.project_dir if session else None,
            "brief": session.working_brief.model_dump(mode="json") if session else None,
            "progress": session.progress.model_dump(mode="json") if session else None,
            "tool_names": [item.get("tool") for item in tools if item.get("event") == "call"],
            "returncode": returncode,
        }
        (self.round_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _transcript_markdown(
    persona: dict[str, str],
    transcript: list[dict[str, str]],
    tools: list[dict[str, Any]],
) -> str:
    lines = [
        f"# {persona['slug']}",
        "",
        f"观众开场：{persona['opening']}",
        f"观众要改：{persona['revision']}",
        "",
        "## 对白（真实 CLI）",
        "",
    ]
    for item in transcript:
        speaker = "观众" if item["role"] == "user" else "意图层"
        lines.append(f"**{speaker}**  {item['content'].strip()}")
        lines.append("")
    lines.extend(["## 工具", ""])
    calls = [item for item in tools if item.get("event") == "call"]
    if not calls:
        lines.append("（这一轮没有工具日志）")
        lines.append("")
        return "\n".join(lines)
    for index, item in enumerate(calls, start=1):
        lines.append(
            f"{index}. `{item.get('tool')}` "
            f"{json.dumps(item.get('arguments') or {}, ensure_ascii=False)}"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="用模拟观众驱动真实 ugc-intake CLI，记录对白、工具和修改前后差别。"
    )
    parser.add_argument("--only", nargs="*", help="只跑这些 slug")
    parser.add_argument("--turn-timeout", type=int, default=900)
    parser.add_argument("--fresh", action="store_true", help="先清空输出目录")
    args = parser.parse_args(argv)
    if args.fresh and OUT_ROOT.exists():
        shutil.rmtree(OUT_ROOT)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    selected = [
        persona
        for persona in PERSONAS
        if not args.only or persona["slug"] in args.only
    ]
    index: list[dict[str, Any]] = []
    for persona in selected:
        print(f"=== {persona['slug']} ===", flush=True)
        summary = CliAudience(OUT_ROOT / persona["slug"], persona).run(
            turn_timeout=args.turn_timeout
        )
        index.append(summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    (OUT_ROOT / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if all(item.get("started") for item in index) else 1


if __name__ == "__main__":
    raise SystemExit(main())
