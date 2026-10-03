"""Capture intake / Generic terminal lines without changing the harness."""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import AppConfig
from .schemas import IntakeToolCall, RunActionView

_MAX_LINE = 2000
_MAX_LINES = 200
_TOOL_LOG_RE = re.compile(
    r"^\[(?P<agent>[^\]]+)\] (?P<event>call|done|failed) (?P<tool>\S+)(?: (?P<rest>.*))?$"
)
_HASH_KEYS = {"content_hash", "dependency_hashes", "input_hash", "sha256"}
_JSON_DECODER = json.JSONDecoder()


def agent_tool_log_path() -> Path:
    raw = os.environ.get("AGENT_TOOL_LOG")
    if raw:
        return Path(raw)
    return AppConfig.default().repo_root / "app" / "data" / "logs" / "agent-tools.log"


def log_offset(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def read_log_delta(path: Path, offset: int) -> list[str]:
    try:
        data = path.read_bytes()
    except OSError:
        return []
    start = offset if offset <= len(data) else 0
    text = data[start:].decode("utf-8", errors="replace")
    lines = [item.rstrip() for item in text.splitlines() if item.strip()]
    return lines[-_MAX_LINES:]


def clip_log_lines(lines: list[str]) -> list[str]:
    return [_clip(item) for item in lines]


def skill_name(tool: str, arguments: dict[str, Any] | None) -> str | None:
    if tool != "skill.activate":
        return None
    name = (arguments or {}).get("name")
    return str(name).strip() or None


def format_intake_line(
    event: str,
    tool: str,
    arguments: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> str:
    parts = [f"[intake] {event} {tool}"]
    if arguments is not None:
        parts.append(json.dumps(arguments, ensure_ascii=False))
    if result is not None:
        if result.get("ok") is False and result.get("error"):
            parts.append(f"ok=false error={result['error']}")
        elif "ok" in result:
            parts.append(f"ok={result['ok']}")
    return _clip(" ".join(parts))


def parse_intake_events(raw: list[dict[str, Any]]) -> tuple[list[IntakeToolCall], list[str]]:
    calls: list[IntakeToolCall] = []
    lines: list[str] = []
    pending: dict[str, IntakeToolCall] = {}
    for item in raw:
        event = str(item.get("event") or "")
        tool = str(item.get("tool") or "")
        arguments = item.get("arguments")
        result = item.get("result")
        args = arguments if isinstance(arguments, dict) else None
        payload = result if isinstance(result, dict) else None
        lines.append(format_intake_line(event, tool, args, payload))
        if event == "call":
            call = IntakeToolCall(
                tool=tool,
                skill=skill_name(tool, args),
                arguments=args or {},
            )
            pending[tool] = call
            calls.append(call)
            continue
        if event == "done" and tool in pending:
            call = pending.pop(tool)
            if payload is not None:
                call.result_ok = payload.get("ok") if "ok" in payload else True
                error = payload.get("error")
                call.error = str(error) if error else None
    return calls, lines


def parse_tool_log_lines(lines: list[str]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}
    for line in lines:
        match = _TOOL_LOG_RE.match(line.strip())
        if match is None:
            continue
        event = match.group("event")
        tool = match.group("tool")
        arguments, duration_ms = _split_log_rest(match.group("rest") or "")
        if event == "call":
            item = {
                "tool": tool,
                "arguments": arguments,
                "duration_ms": duration_ms,
                "skill": skill_name(tool, arguments),
            }
            pending[tool] = item
            calls.append(item)
            continue
        current = pending.pop(tool, None)
        if current is None:
            continue
        if arguments and not current["arguments"]:
            current["arguments"] = arguments
        if duration_ms and not current["duration_ms"]:
            current["duration_ms"] = duration_ms
        if not current.get("skill"):
            current["skill"] = skill_name(tool, arguments)
    return calls


def merge_action_arguments(
    actions: list[RunActionView],
    lines: list[str],
) -> list[RunActionView]:
    parsed = parse_tool_log_lines(lines)
    if not actions:
        return [
            RunActionView(
                tool=str(item["tool"]),
                result="success",
                duration_ms=int(item.get("duration_ms") or 0),
                skill=item.get("skill"),
                arguments=dict(item.get("arguments") or {}),
            )
            for item in parsed
        ]
    unused = list(parsed)
    merged: list[RunActionView] = []
    for action in actions:
        updated = action.model_copy(deep=True)
        index = next(
            (i for i, item in enumerate(unused) if item["tool"] == action.tool),
            None,
        )
        if index is not None:
            hit = unused.pop(index)
            if not updated.arguments:
                updated.arguments = dict(hit.get("arguments") or {})
            if not updated.skill:
                updated.skill = hit.get("skill")
            if not updated.duration_ms and hit.get("duration_ms"):
                updated.duration_ms = int(hit["duration_ms"])
        merged.append(updated)
    return merged


def injected_context(
    task: Any | None,
    *,
    instruction: str | None = None,
    project_dir: Path | None = None,
    stage: str | None = None,
    existing: dict[str, Any] | None = None,
    state: Any | None = None,
) -> dict[str, Any]:
    payload = dict(existing or {})
    if task is not None and "task" not in payload:
        payload["task"] = sanitize_context(task.model_dump(mode="json"))
    note = (instruction or "").strip()
    if note:
        payload["instruction"] = note
    if project_dir is not None and stage:
        extras = _stage_extras(
            project_dir,
            stage,
            task,
            state=state,
            instruction=note,
        )
        for key, value in extras.items():
            if key in {"asset_context", "references_context"}:
                payload[key] = value
            else:
                payload.setdefault(key, value)
    return payload


def run_opening_messages(
    task: Any | None,
    *,
    instruction: str | None = None,
    project_dir: Path | None = None,
    stage: str | None = None,
    state: Any | None = None,
    existing: dict[str, Any] | None = None,
    existing_messages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if existing_messages:
        return [dict(item) for item in existing_messages if isinstance(item, dict)]
    note = (instruction or "").strip()
    if stage == "narrative":
        return _narrative_opening_messages(
            task,
            instruction=note,
            project_dir=project_dir,
            state=state,
            existing=existing,
        )
    if stage not in {"asset", "references"}:
        return []
    extras = (
        _stage_extras(
            project_dir,
            stage,
            task,
            state=state,
            instruction=note,
        )
        if project_dir is not None
        else {}
    )
    pack = extras.get("asset_context") or extras.get("references_context") or {}
    stored = {}
    if isinstance(existing, dict):
        stored = existing.get("asset_context") or existing.get("references_context") or {}
    if not isinstance(pack, dict):
        pack = {}
    if not isinstance(stored, dict):
        stored = {}
    if not pack.get("memory") and stored.get("memory"):
        pack = stored
    memory = pack.get("memory") if isinstance(pack.get("memory"), dict) else {}
    turn = pack.get("turn") if isinstance(pack.get("turn"), dict) else {
        "instruction": note,
        "regenerate_identity_refs": _log_regenerate_identities(task),
        "regenerate_shot_ids": list(pack.get("scoped_shot_ids") or []),
        "identity_only": bool(pack.get("identity_only") or stage == "references"),
    }
    from ugc_harness.agents.instructions import compose_opening_messages, load_instructions

    return compose_opening_messages(
        system=load_instructions("shot_asset"),
        agent="asset_stage",
        memory=memory,
        user_payload=turn,
    )


def sanitize_context(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: sanitize_context(item)
            for key, item in value.items()
            if key not in _HASH_KEYS
        }
    if isinstance(value, list):
        return [sanitize_context(item) for item in value]
    return value


def _split_log_rest(rest: str) -> tuple[dict[str, Any], int]:
    text = rest.strip()
    arguments: dict[str, Any] = {}
    if text.startswith("{"):
        try:
            payload, end = _JSON_DECODER.raw_decode(text)
        except json.JSONDecodeError:
            payload, end = {}, 0
        if isinstance(payload, dict):
            arguments = payload
        text = text[end:].strip()
    duration = 0
    matched = re.match(r"^(\d+)ms\b", text)
    if matched:
        duration = int(matched.group(1))
    return arguments, duration


def _stage_extras(
    project_dir: Path,
    stage: str,
    task: Any | None,
    *,
    state: Any | None = None,
    instruction: str = "",
) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    scope = getattr(task, "scope", None)
    refs = list(getattr(scope, "target_refs", None) or [])
    shots = list(getattr(scope, "shot_ids", None) or [])
    loaded = _ensure_state(project_dir, state)
    if stage == "narrative":
        brief = _artifact_brief(project_dir)
        if brief:
            extras["creative_brief"] = brief
        extras["narrative_context"] = _narrative_opening_context(
            project_dir,
            task,
            state=loaded,
            instruction=instruction,
        )
        if refs or shots:
            extras["repair_pack"] = {
                "target_refs": refs,
                "shot_ids": shots,
            }
    elif stage == "voice":
        extras["voice_context"] = _voice_context(project_dir)
    elif stage == "references":
        extras["references_context"] = _asset_opening_context(
            project_dir,
            task,
            state=loaded,
            instruction=instruction,
            identity_only=True,
            extra={
                "identity_count": _identity_count(loaded),
                "target_refs": refs,
            },
        )
    elif stage == "asset":
        extras["asset_context"] = _asset_opening_context(
            project_dir,
            task,
            state=loaded,
            instruction=instruction,
            identity_only=_identity_only_task(task, shots),
        )
    return {key: value for key, value in extras.items() if value}


def _narrative_opening_context(
    project_dir: Path | None,
    task: Any | None,
    *,
    state: Any | None,
    instruction: str,
) -> dict[str, Any]:
    if task is None:
        return {}
    from ugc_harness.agents.narrative_agent.memory import narrative_opening_context
    from ugc_harness.agents.narrative_agent.models import CreativeBrief

    brief_payload = _artifact_brief(project_dir) if project_dir is not None else None
    if brief_payload is None and state is not None:
        record = getattr(getattr(state, "creative", None), "brief", None)
        if record is not None:
            brief_payload = record.model_dump(mode="json")
    if not isinstance(brief_payload, dict):
        return {}
    try:
        brief = CreativeBrief.model_validate(brief_payload)
    except Exception:
        return {}
    loaded = state
    if loaded is None and project_dir is not None:
        loaded = _ensure_state(project_dir, None)
    return narrative_opening_context(
        task,
        brief=brief,
        prior=None,
        state=loaded,
        instruction=instruction or None,
    )


def _narrative_opening_messages(
    task: Any | None,
    *,
    instruction: str,
    project_dir: Path | None,
    state: Any | None,
    existing: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if task is None:
        return []
    pack = {}
    if isinstance(existing, dict):
        stored = existing.get("narrative_context")
        if isinstance(stored, dict):
            pack = stored
    if not pack.get("memory"):
        pack = _narrative_opening_context(
            project_dir,
            task,
            state=state,
            instruction=instruction,
        )
    memory = pack.get("memory") if isinstance(pack.get("memory"), dict) else {}
    turn = pack.get("turn") if isinstance(pack.get("turn"), dict) else {
        "instruction": instruction,
        "target_refs": list(getattr(getattr(task, "scope", None), "target_refs", []) or []),
    }
    from ugc_harness.agents.instructions import compose_opening_messages, system_prompt_for_task

    try:
        system = system_prompt_for_task(task)
    except Exception:
        from ugc_harness.agents.instructions import load_instructions

        system = load_instructions("narrative_drama")
    return compose_opening_messages(
        system=system,
        agent="narrative_agent",
        memory=memory,
        user_payload=turn,
    )


def _artifact_brief(project_dir: Path) -> dict[str, Any] | None:
    payload = _read_json(project_dir / "narrative_artifact.json")
    brief = payload.get("brief") if isinstance(payload, dict) else None
    return sanitize_context(brief) if isinstance(brief, dict) else None


def _voice_context(project_dir: Path) -> dict[str, Any]:
    payload = _read_json(project_dir / "narrative_artifact.json")
    if not isinstance(payload, dict):
        return {}
    brief = payload.get("brief") if isinstance(payload.get("brief"), dict) else {}
    planning = payload.get("planning") if isinstance(payload.get("planning"), dict) else {}
    world = planning.get("world_state") if isinstance(planning.get("world_state"), dict) else {}
    character = world.get("aroll_character")
    profile = (
        character.get("voice_profile")
        if isinstance(character, dict) and isinstance(character.get("voice_profile"), dict)
        else {}
    )
    script = payload.get("script") if isinstance(payload.get("script"), dict) else {}
    segments = script.get("segments") if isinstance(script.get("segments"), list) else []
    return {
        "project_id": brief.get("project_id"),
        "voice_id": None,
        "voice_selection": "catalog",
        "speaker_gender": profile.get("gender"),
        "speaker_age_style": profile.get("age_style"),
        "script_segment_count": len(segments),
    }


def _asset_context(project_dir: Path, scoped_shot_ids: list[str]) -> dict[str, Any]:
    return _asset_opening_context(
        project_dir,
        None,
        state=None,
        instruction="",
        identity_only=False,
        extra={"scoped_shot_ids": list(scoped_shot_ids)},
    )


def _asset_opening_context(
    project_dir: Path,
    task: Any | None,
    *,
    state: Any | None,
    instruction: str,
    identity_only: bool,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    loaded = _ensure_state(project_dir, state)
    scoped_shots = _log_scoped_shots(loaded, task, identity_only)
    if extra and extra.get("scoped_shot_ids") and not scoped_shots:
        scoped_shots = list(extra["scoped_shot_ids"])
    scoped_identities = _log_scoped_identities(loaded, task, scoped_shots)
    memory: dict[str, Any] = {}
    if loaded is not None:
        from ugc_harness.harness.shot_asset_controller import _asset_memory

        memory = _asset_memory(
            loaded, project_dir, scoped_identities, scoped_shots
        )
    turn = {
        "instruction": instruction.strip(),
        "regenerate_identity_refs": _log_regenerate_identities(task),
        "regenerate_shot_ids": list(scoped_shots),
        "identity_only": identity_only,
    }
    payload = _read_json(project_dir / "narrative_artifact.json")
    brief = payload.get("brief") if isinstance(payload, dict) else {}
    shots_wrap = payload.get("shots") if isinstance(payload, dict) else None
    shots = shots_wrap.get("shots") if isinstance(shots_wrap, dict) else []
    count = len(shots) if isinstance(shots, list) else len(scoped_shots)
    project_id = brief.get("project_id") if isinstance(brief, dict) else None
    if not project_id:
        project_id = _project_id(project_dir, loaded)
    if (
        not project_id
        and not count
        and not scoped_shots
        and not scoped_identities
        and not memory
    ):
        return {}
    opening = {
        "project_id": project_id,
        "shot_count": count,
        "scoped_shot_ids": list(scoped_shots),
        "identity_only": identity_only,
        "memory": memory,
        "turn": turn,
    }
    if extra:
        for key, value in extra.items():
            if key not in opening or opening[key] in (None, "", [], {}):
                opening[key] = value
    return sanitize_context(opening)


def _identity_only_task(task: Any | None, shot_ids: list[str]) -> bool:
    digest = str(getattr(task, "input_hash", "") or "")
    if digest == "identity-only":
        return True
    return digest.startswith("repair-") and not shot_ids


def _log_scoped_shots(
    state: Any | None,
    task: Any | None,
    identity_only: bool,
) -> list[str]:
    if identity_only:
        return []
    scope = getattr(task, "scope", None)
    shots = list(getattr(scope, "shot_ids", None) or [])
    if shots:
        return shots
    if state is None:
        return []
    return [shot.spec.shot_id for shot in getattr(state.production, "shots", []) or []]


def _log_scoped_identities(
    state: Any | None,
    task: Any | None,
    scoped_shots: list[str],
) -> list[str]:
    if state is None:
        return []
    if task is not None:
        from ugc_harness.harness.shot_asset_controller import _scoped_identity_refs

        return _scoped_identity_refs(state, task, scoped_shots)
    from ugc_harness.harness.world_identities import bound_identity_refs

    if not scoped_shots:
        return bound_identity_refs(state)
    found: list[str] = []
    wanted = set(scoped_shots)
    for shot in getattr(state.production, "shots", []) or []:
        if shot.spec.shot_id not in wanted:
            continue
        for bind in shot.spec.binds:
            if bind not in found:
                found.append(bind)
    return found or bound_identity_refs(state)


def _log_regenerate_identities(task: Any | None) -> list[str]:
    digest = str(getattr(task, "input_hash", "") or "")
    if digest != "repair-asset":
        return []
    scope = getattr(task, "scope", None)
    return [
        ref
        for ref in list(getattr(scope, "target_refs", None) or [])
        if str(ref).startswith(("cast:", "prop:", "place:"))
    ]


def _ensure_state(project_dir: Path, state: Any | None) -> Any | None:
    if state is not None:
        return state
    path = project_dir / "harness" / "project_state.json"
    if not path.is_file():
        return None
    try:
        from ugc_harness.harness.models import ProjectState

        return ProjectState.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _project_id(project_dir: Path, state: Any | None) -> str | None:
    if state is not None:
        context = getattr(state, "context", None)
        value = getattr(context, "project_id", None)
        if value:
            return str(value)
    brief = _artifact_brief(project_dir) or {}
    value = brief.get("project_id")
    return str(value) if value else None


def _identity_count(state: Any | None) -> int:
    if state is None:
        return 0
    world = getattr(getattr(state, "production", None), "world", None)
    if world is None:
        return 0
    return sum(
        len(getattr(world, name, None) or [])
        for name in ("cast", "props", "places")
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def received_task_message(task: Any, feedback: str | None) -> str:
    parts: list[str] = []
    note = (feedback or "").strip()
    if note:
        parts.append(note)
    goal = str(getattr(task, "goal", "") or "").strip()
    if goal and goal not in parts:
        parts.append(goal)
    scope = getattr(task, "scope", None)
    refs = list(getattr(scope, "target_refs", None) or [])
    shots = list(getattr(scope, "shot_ids", None) or [])
    if refs:
        parts.append("targets: " + ", ".join(refs))
    if shots:
        parts.append("shots: " + ", ".join(shots))
    tools = list(getattr(task, "allowed_tools", None) or [])
    if tools:
        parts.append("allowed_tools: " + ", ".join(tools))
    return "\n".join(parts)


@contextmanager
def capture_intake_log(path: Path) -> Iterator[list[dict[str, Any]]]:
    previous = os.environ.get("INTAKE_TOOL_LOG")
    path.parent.mkdir(parents=True, exist_ok=True)
    os.environ["INTAKE_TOOL_LOG"] = str(path)
    offset = log_offset(path)
    bucket: list[dict[str, Any]] = []
    try:
        yield bucket
        bucket.extend(_read_jsonl(path, offset))
    finally:
        if previous is None:
            os.environ.pop("INTAKE_TOOL_LOG", None)
        else:
            os.environ["INTAKE_TOOL_LOG"] = previous


def _read_jsonl(path: Path, offset: int) -> list[dict[str, Any]]:
    try:
        data = path.read_bytes()
    except OSError:
        return []
    start = offset if offset <= len(data) else 0
    text = data[start:].decode("utf-8", errors="replace")
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _clip(value: str) -> str:
    if len(value) <= _MAX_LINE:
        return value
    return value[: _MAX_LINE - 1] + "…"
