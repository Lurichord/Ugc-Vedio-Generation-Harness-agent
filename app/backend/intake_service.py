from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from ugc_harness.intake.agent import _AGENT_MD, IntentAgent, build_agent_messages
from ugc_harness.intake.brief_sync import HeuristicBriefSync
from ugc_harness.intake.host import IntentHost, append_message, new_session, record_user_text
from ugc_harness.intake.mcp_runtime import IntakeMcpRuntime
from ugc_harness.intake.models import Inbound, IntakeSession
from ugc_harness.intake.view import refresh_session_from_disk
from ugc_harness.shared.llm import StructuredLLM
from ugc_harness.shared.settings import LLMSettings

from .beat_projector import BeatProjector, stage_refs
from .intake_store import IntakeStore, ProjectSessionIndex
from .intake_workspace import IntakeWorkspaceStore, set_gate
from .jobs import JobManager
from .production_view import (
    STAGE_LABELS,
    ProductionView,
    gate_question,
    next_stage_after,
)
from .agent_trace import capture_intake_log, parse_intake_events
from .project_service import ProjectService, production_pipeline
from .review_service import ReviewService
from .run_log_projector import RunLogProjector, empty_run_log
from .schemas import (
    CreateIntakeSessionResponse,
    IntakeMessageResponse,
    IntakeTurnRecord,
    IntakeWorkspaceState,
    PendingGate,
    RunStageRequest,
    StageName,
    TaskEvent,
)
from .state_projector import StateProjector, empty_project_state_view
from .stage_runner import StageRunner

_CONTINUE = re.compile(
    r"^(可以|继续|好的?|行|没问题|一样|下一步|开始吧|做吧|ok|yes|y)"
    r"([，,。.!！ ]*(继续|下一步|开始|做吧|一样)?)?$",
    re.IGNORECASE,
)
_REVISE = re.compile(r"(改|重做|不对|不好|太|缩短|加长|重来|修)")


class IntakeService:
    def __init__(
        self,
        store: IntakeStore,
        workspace: IntakeWorkspaceStore,
        runner: StageRunner,
        projects: ProjectService,
        reviews: ReviewService,
        beats: BeatProjector,
        production: ProductionView,
        jobs: JobManager,
        repository,
        agent: IntentAgent | None = None,
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.runner = runner
        self.projects = projects
        self.reviews = reviews
        self.beats = beats
        self.production = production
        self.jobs = jobs
        self.repository = repository
        self.project_sessions = ProjectSessionIndex(
            store.root / "project_sessions.json"
        )
        self._agent = agent
        self._llm: StructuredLLM | None = None

    def create_session(self) -> CreateIntakeSessionResponse:
        session = self._spawn_session()
        return self.session_payload(session.session_id)

    def _spawn_session(self) -> IntakeSession:
        session = new_session()
        self.store.save(session)
        self.workspace.save(IntakeWorkspaceState(session_id=session.session_id))
        return session

    def get_session(self, session_id: str) -> IntakeSession:
        return self.store.load(session_id)

    def session_payload(self, session_id: str) -> CreateIntakeSessionResponse:
        session = self.store.load(session_id)
        state = self._sync_workspace(session)
        return CreateIntakeSessionResponse(**self._payload(session, state))

    def handle_message(self, session_id: str, message: str) -> IntakeMessageResponse:
        session = self.store.load(session_id)
        state = self._sync_workspace(session)
        if state.pending_gate and state.pending_gate.kind == "running":
            raise ValueError("这一步还在制作，先等它做完。")
        text = message.strip()
        session = record_user_text(session, text)
        self.store.save(session)
        log_path = self.store.root / f"{session_id}.tools.jsonl"
        with capture_intake_log(log_path) as events:
            result = self._host(session_id).respond(session, Inbound(text=text))
        self.store.save(result.session)
        state = self._sync_workspace(result.session)
        if state.project_key:
            self.project_sessions.bind(state.project_key, result.session.session_id)
        self._record_intake_turn(
            session_id,
            state.project_key,
            received=text,
            reply=result.message,
            events=events,
            session=result.session,
        )
        return self._turn_payload(
            result.session,
            state,
            result.message,
            done=result.done,
        )

    def continue_production(
        self,
        session_id: str,
        *,
        user_text: str | None = None,
    ) -> IntakeMessageResponse:
        session = self.store.load(session_id)
        state = self._sync_workspace(session)
        if user_text:
            session = record_user_text(session, user_text)
            self.store.save(session)
            state = self.workspace.add_notice(state, user_text, role="user")
        if not state.project_key:
            raise ValueError("还没有项目，不能进入下一阶段")
        gate = state.pending_gate
        if gate and gate.kind == "running":
            return self._turn_payload(
                session, state, "这一步还在跑。", job_id=gate.job_id
            )
        if gate and gate.kind == "done":
            state = self.workspace.add_notice(state, "成片已经在右侧了。")
            return self._turn_payload(session, state, state.notices[-1].content)
        directory = self.projects.project_dir(state.project_key)
        project_state = self.projects.read_state(directory)
        pipeline = list(production_pipeline(project_state, directory))
        stage = (
            gate.stage
            if gate and gate.kind != "failed" and gate.stage in pipeline
            else None
        )
        stage = stage or self.projects.current_stage(
            project_state, state.project_key
        )
        view = self.beats.stage_view(directory, stage)
        status = _runnable_status(view.core_status, stage, pipeline)
        if status in {"pending", "ready", "failed", "needs_revision", "stale", "blocked"}:
            return self._start_stage_job(session, state, directory, stage)
        if status in {"passed", "locked"} and not view.user_approved:
            if not view.critic_passed and view.issues:
                raise ValueError("这一步还不能批准：Critic 未通过")
            if self.reviews.open_feedback(state.project_key, stage):
                raise ValueError("还有未解决的反馈，先改这一步")
            beat_ids = [item.beat_id for item in view.beats if item.artifacts] or [
                item.beat_id for item in view.beats
            ] or ["all"]
            self.reviews.approve(
                state.project_key,
                stage,
                project_state,
                beat_ids,
                stage_refs(view),
            )
            self.repository.add_event(
                state.project_key,
                TaskEvent(
                    event_id=f"event_{uuid4().hex}",
                    project_id=project_state.context.project_id,
                    stage=stage,
                    event_type="user_approved",
                    beat_ids=beat_ids,
                    message=f"用户确认 {stage}",
                ),
            )
        nxt = next_stage_after(stage, pipeline=pipeline)
        if nxt is None:
            question = gate_question(stage, None)
            state = set_gate(
                state,
                PendingGate(kind="done", stage=stage, question=question),
            )
            self.workspace.save(state)
            state = self.workspace.add_notice(state, question)
            return self._turn_payload(session, state, question)
        return self._start_stage_job(session, state, directory, nxt)

    def attach_project(self, session_id: str, project_key: str) -> CreateIntakeSessionResponse:
        directory = self.projects.project_dir(project_key)
        current = self.store.load(session_id)
        current_state = self.workspace.load(session_id)
        if self._session_project_key(current, current_state) == project_key:
            self.project_sessions.bind(project_key, current.session_id)
            return self.session_payload(current.session_id)
        existing = self._session_id_for_project(project_key)
        if existing:
            return self.session_payload(existing)
        session = (
            current
            if self._is_fresh_chat(current, current_state)
            else self._spawn_session()
        )
        return self._bind_session_to_project(session, project_key, directory)

    def _bind_session_to_project(
        self,
        session: IntakeSession,
        project_key: str,
        directory: Path,
    ) -> CreateIntakeSessionResponse:
        project_state = self.projects.read_state(directory)
        session = session.model_copy(
            update={
                "project_dir": str(directory),
                "project_id": project_state.context.project_id,
            }
        )
        session = refresh_session_from_disk(session)
        note = (
            f"已打开「{self._project_label(project_key)}」。"
            "右侧是这个项目已经做完的内容，可以直接说要改哪一段。"
        )
        if not any(item.content == note for item in session.messages):
            session = append_message(session, "agent", note)
            session = session.model_copy(update={"last_message": note})
        self.store.save(session)
        state = self.workspace.load(session.session_id)
        state = state.model_copy(
            update={"project_key": project_key, "session_id": session.session_id}
        )
        state = self._apply_gate_from_project(state, directory)
        self.workspace.save(state)
        if state.pending_gate and state.pending_gate.kind == "review":
            if not any(
                item.content == state.pending_gate.question for item in state.notices
            ):
                state = self.workspace.add_notice(state, state.pending_gate.question)
        self.project_sessions.bind(project_key, session.session_id)
        return CreateIntakeSessionResponse(**self._payload(session, state))

    def _session_project_key(
        self,
        session: IntakeSession,
        state: IntakeWorkspaceState,
    ) -> str | None:
        if state.project_key:
            return state.project_key
        if session.project_dir:
            return Path(session.project_dir).name
        return None

    def _is_fresh_chat(
        self,
        session: IntakeSession,
        state: IntakeWorkspaceState,
    ) -> bool:
        if self._session_project_key(session, state):
            return False
        return all(item.role != "user" for item in session.messages)

    def _session_id_for_project(self, project_key: str) -> str | None:
        bound = self.project_sessions.session_id(project_key)
        if bound and self.store.path_for(bound).is_file():
            return bound
        if bound:
            self.project_sessions.unbind(project_key)
        found: list[tuple[str, str]] = []
        if self.workspace.root.is_dir():
            for path in self.workspace.root.glob("*.workspace.json"):
                try:
                    state = IntakeWorkspaceState.model_validate_json(
                        path.read_text(encoding="utf-8")
                    )
                except (OSError, ValueError):
                    continue
                if state.project_key != project_key:
                    continue
                try:
                    session = self.store.load(state.session_id)
                except FileNotFoundError:
                    continue
                found.append((session.updated_at, session.session_id))
        if self.store.root.is_dir():
            for path in self.store.root.glob("intake_*.json"):
                try:
                    session = IntakeSession.model_validate_json(
                        path.read_text(encoding="utf-8")
                    )
                except (OSError, ValueError):
                    continue
                if not session.project_dir:
                    continue
                if Path(session.project_dir).name != project_key:
                    continue
                found.append((session.updated_at, session.session_id))
        if not found:
            return None
        found.sort()
        session_id = found[-1][1]
        self.project_sessions.bind(project_key, session_id)
        return session_id

    def _project_label(self, project_key: str) -> str:
        try:
            directory = self.projects.project_dir(project_key)
            manifest = self.projects.read_json(directory, "manifest.json")
        except (FileNotFoundError, OSError, ValueError):
            return project_key
        name = manifest.get("project_name")
        return str(name) if name else project_key

    def _start_stage_job(
        self,
        session: IntakeSession,
        state: IntakeWorkspaceState,
        directory: Path,
        stage: StageName,
    ) -> IntakeMessageResponse:
        project_id = self.projects.read_state(directory).context.project_id
        label = STAGE_LABELS[stage]
        note = f"开始制作{label}，做完后会停下来问你。"
        session, started = self._append_progress(session, state, note)
        self._record_intake_turn(
            session.session_id,
            started.project_key,
            received=session.messages[-2].content
            if len(session.messages) >= 2 and session.messages[-2].role == "user"
            else "可以",
            reply=note,
            events=[],
            session=session,
        )
        job = self.jobs.submit(
            f"执行 {stage}",
            lambda sid=started.session_id, d=directory, s=stage, pid=project_id: (
                self._run_stage(sid, d, s, pid)
            ),
        )
        running = PendingGate(
            kind="running",
            stage=stage,
            question=f"{label}正在生成，右侧会陆续出现内容。",
            job_id=job["job_id"],
        )
        started = set_gate(started, running)
        self.workspace.save(started)
        return self._turn_payload(
            session,
            started,
            started.notices[-1].content,
            job_id=job["job_id"],
            harness_action=f"run_{stage}",
        )

    def _run_stage(
        self,
        session_id: str,
        directory: Path,
        stage: StageName,
        project_id: str,
    ) -> dict[str, Any]:
        self.repository.add_event(
            directory.name,
            TaskEvent(
                event_id=f"event_{uuid4().hex}",
                project_id=project_id,
                stage=stage,
                event_type="started",
                message=f"开始执行 {stage}",
            ),
        )
        try:
            self.runner.run(stage, directory, RunStageRequest())
        except Exception as exc:
            state = self.workspace.load(session_id)
            failed = PendingGate(
                kind="failed",
                stage=stage,
                question=f"{STAGE_LABELS[stage]}失败了：{exc}",
                error=str(exc),
            )
            state = set_gate(state, failed)
            self.workspace.save(state)
            self._announce(session_id, failed.question)
            self.repository.add_event(
                directory.name,
                TaskEvent(
                    event_id=f"event_{uuid4().hex}",
                    project_id=project_id,
                    stage=stage,
                    event_type="failed",
                    message=str(exc),
                ),
            )
            raise
        state = self.workspace.load(session_id)
        project_state = self.projects.read_state(directory)
        nxt = next_stage_after(
            stage,
            pipeline=list(production_pipeline(project_state, directory)),
        )
        question = gate_question(stage, nxt)
        state = set_gate(
            state,
            PendingGate(
                kind="review",
                stage=stage,
                next_stage=nxt,
                question=question,
            ),
        )
        self.workspace.save(state)
        self._announce(session_id, question)
        self.repository.add_event(
            directory.name,
            TaskEvent(
                event_id=f"event_{uuid4().hex}",
                project_id=project_id,
                stage=stage,
                event_type="awaiting_review",
                message=question,
            ),
        )
        return {"project_id": project_id, "stage": stage}

    def _announce(self, session_id: str, content: str) -> None:
        session = self.store.load(session_id)
        state = self.workspace.load(session_id)
        self._append_progress(session, state, content)

    def _append_progress(
        self,
        session: IntakeSession,
        state: IntakeWorkspaceState,
        content: str,
    ) -> tuple[IntakeSession, IntakeWorkspaceState]:
        if (
            session.messages
            and session.messages[-1].role == "agent"
            and session.messages[-1].content == content
        ):
            return session, state
        session = append_message(session, "agent", content)
        session = session.model_copy(update={"last_message": content})
        self.store.save(session)
        state = self.workspace.add_notice(state, content)
        return session, state

    def _sync_workspace(self, session: IntakeSession) -> IntakeWorkspaceState:
        state = self.workspace.load(session.session_id)
        project_key = state.project_key
        if session.project_dir:
            path = Path(session.project_dir)
            if path.is_dir():
                project_key = path.name
        if project_key != state.project_key:
            state = state.model_copy(update={"project_key": project_key})
            self.workspace.save(state)
        if project_key and (state.pending_gate is None or state.pending_gate.kind == "failed"):
            try:
                directory = self.projects.project_dir(project_key)
            except FileNotFoundError:
                return state
            state = self._apply_gate_from_project(state, directory)
            self.workspace.save(state)
        elif project_key and state.pending_gate and state.pending_gate.kind == "running":
            job_id = state.pending_gate.job_id
            if job_id:
                try:
                    job = self.jobs.get(job_id)
                except KeyError:
                    job = None
                if job and job["status"] == "completed":
                    directory = self.projects.project_dir(project_key)
                    state = self._apply_gate_from_project(state, directory)
                    self.workspace.save(state)
        return self.workspace.load(session.session_id)

    def _apply_gate_from_project(
        self,
        state: IntakeWorkspaceState,
        directory: Path,
    ) -> IntakeWorkspaceState:
        project_state = self.projects.read_state(directory)
        pipeline = list(production_pipeline(project_state, directory))
        current = self.projects.current_stage(project_state, directory.name)
        view = self.beats.stage_view(directory, current)
        status = _runnable_status(view.core_status, current, pipeline)
        if status == "running":
            return set_gate(
                state,
                PendingGate(
                    kind="running",
                    stage=current,
                    question=f"{STAGE_LABELS[current]}正在生成。",
                    job_id=state.pending_gate.job_id if state.pending_gate else None,
                ),
            )
        if status in {"passed", "locked"} and not view.user_approved:
            nxt = next_stage_after(current, pipeline=pipeline)
            return set_gate(
                state,
                PendingGate(
                    kind="review",
                    stage=current,
                    next_stage=nxt,
                    question=gate_question(current, nxt),
                ),
            )
        if status in {"pending", "ready", "failed", "needs_revision", "stale", "blocked"}:
            previous = _previous_required(current, pipeline)
            if previous and self.reviews.valid_approval(
                directory.name, previous, project_state
            ):
                return set_gate(
                    state,
                    PendingGate(
                        kind="review",
                        stage=previous,
                        next_stage=current,
                        question=gate_question(previous, current),
                    ),
                )
        if current == "render" and view.user_approved:
            return set_gate(
                state,
                PendingGate(
                    kind="done",
                    stage="render",
                    question=gate_question("render", None),
                ),
            )
        return state

    def _payload(
        self,
        session: IntakeSession,
        state: IntakeWorkspaceState,
        **extra: Any,
    ) -> dict[str, Any]:
        reply = extra.get("reply")
        if reply is None:
            reply = session.last_message or ""
        return {
            "session_id": session.session_id,
            "status": session.status,
            "reply": reply,
            "address": extra.get("address")
            or ("done" if session.status == "done" else "user"),
            "check_ok": extra.get("check_ok", True),
            "issues": extra.get("issues") or [],
            "project_id": session.project_id,
            "project_key": state.project_key,
            "job_id": extra.get("job_id")
            or (state.pending_gate.job_id if state.pending_gate else None),
            "harness_action": extra.get("harness_action"),
            "messages": [item.model_dump(mode="json") for item in session.messages],
            "brief": session.working_brief.model_dump(mode="json"),
            "intent": session.working_intent.model_dump(mode="json"),
            "progress": session.progress.model_dump(mode="json"),
            "production": self.production.snapshot(state.project_key).model_dump(
                mode="json"
            ),
            "project_state": self._project_state_view(state.project_key),
            "run_log": self._run_log_view(state.project_key, session.session_id),
            "gate": (
                state.pending_gate.model_dump(mode="json")
                if state.pending_gate
                else None
            ),
            "notices": [item.model_dump(mode="json") for item in state.notices],
        }

    def _project_state_view(self, project_key: str | None) -> dict[str, Any]:
        if not project_key:
            return empty_project_state_view().model_dump(mode="json")
        try:
            directory = self.projects.project_dir(project_key)
        except FileNotFoundError:
            return empty_project_state_view(project_key).model_dump(mode="json")
        return StateProjector(self.projects).view(directory).model_dump(mode="json")

    def _run_log_view(
        self, project_key: str | None, session_id: str
    ) -> dict[str, Any]:
        turns = self._merge_intake_turns(session_id, project_key)
        if not project_key:
            return empty_run_log(intake_turns=turns).model_dump(mode="json")
        try:
            directory = self.projects.project_dir(project_key)
        except FileNotFoundError:
            return empty_run_log(project_key, turns).model_dump(mode="json")
        view = RunLogProjector(self.projects, self.repository).view(directory)
        return view.model_copy(update={"intake_turns": turns}).model_dump(mode="json")

    def _record_intake_turn(
        self,
        session_id: str,
        project_key: str | None,
        *,
        received: str,
        reply: str,
        events: list[dict[str, Any]],
        session: IntakeSession | None = None,
    ) -> None:
        tools, lines = parse_intake_events(events)
        record = IntakeTurnRecord(
            turn_id=f"turn_{uuid4().hex}",
            session_id=session_id,
            project_key=project_key,
            received=received,
            reply=reply,
            tools=tools,
            lines=lines,
            messages=_message_context(session, reply),
        )
        self._save_session_turn(session_id, record)
        if project_key:
            try:
                self.repository.add_intake_turn(project_key, record)
            except Exception:
                return

    def _session_turns_path(self, session_id: str) -> Path:
        return self.store.root / f"{session_id}.turns.json"

    def _load_session_turns(self, session_id: str) -> list[IntakeTurnRecord]:
        path = self._session_turns_path(session_id)
        if not path.is_file():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        items = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return []
        return [IntakeTurnRecord.model_validate(item) for item in items]

    def _save_session_turn(self, session_id: str, record: IntakeTurnRecord) -> None:
        items = [
            item.model_dump(mode="json")
            for item in self._load_session_turns(session_id)
        ]
        dumped = record.model_dump(mode="json")
        for index, item in enumerate(items):
            if item.get("turn_id") == record.turn_id:
                items[index] = dumped
                break
        else:
            items.append(dumped)
        path = self._session_turns_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"items": items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _merge_intake_turns(
        self, session_id: str, project_key: str | None
    ) -> list[IntakeTurnRecord]:
        merged: dict[str, IntakeTurnRecord] = {}
        for item in self._load_session_turns(session_id):
            merged[item.turn_id] = item
        if project_key:
            for item in self.repository.intake_turns(project_key):
                merged[item.turn_id] = item
        return sorted(merged.values(), key=lambda item: item.created_at or "")

    def _turn_payload(
        self,
        session: IntakeSession,
        state: IntakeWorkspaceState,
        reply: str,
        *,
        done: bool = False,
        job_id: str | None = None,
        harness_action: str | None = None,
    ) -> IntakeMessageResponse:
        return IntakeMessageResponse(
            **self._payload(
                session,
                state,
                reply=reply,
                address="done" if done else "user",
                check_ok=True,
                job_id=job_id,
                harness_action=harness_action,
            )
        )

    def _host(self, session_id: str) -> IntentHost:
        return IntentHost(
            self._get_agent(),
            IntakeMcpRuntime(
                self.store.path_for(session_id),
                output_root=self.runner.output_root,
            ),
            brief_sync=HeuristicBriefSync(),
        )

    def _get_agent(self) -> IntentAgent:
        if self._agent is None:
            self._agent = IntentAgent(self._get_llm())
        return self._agent

    def _get_llm(self) -> StructuredLLM:
        if self._llm is None:
            self._llm = StructuredLLM(LLMSettings.from_environment(None, None))
        return self._llm


def _message_context(
    session: IntakeSession | None, reply: str
) -> list[dict[str, Any]]:
    if session is None:
        return []
    messages = list(session.messages)
    if messages and messages[-1].role == "agent" and messages[-1].content == reply:
        session = session.model_copy(update={"messages": messages[:-1]})
    return [
        {"role": item["role"], "content": item.get("content") or ""}
        for item in build_agent_messages(session, _AGENT_MD)
    ]


def _wants_continue(text: str) -> bool:
    cleaned = text.strip()
    if _CONTINUE.fullmatch(cleaned):
        return True
    if _REVISE.search(cleaned):
        return False
    return cleaned in {"可以继续", "一样就行", "就这样", "不用改"}


def _runnable_status(
    status: str,
    stage: StageName,
    pipeline: list[StageName],
) -> str:
    if status == "not_required" and stage in pipeline:
        return "pending"
    return status


def _previous_required(
    stage: StageName,
    pipeline: list[StageName],
) -> StageName | None:
    try:
        index = pipeline.index(stage)
    except ValueError:
        return None
    if index == 0:
        return None
    return pipeline[index - 1]
