"""Generate identity stills and bind them onto cast/prop/place nodes."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field

from ..agents.generic import (
    CompletionSpec,
    EnvironmentToolModel,
    GenericAgent,
    RegistryTool,
    RegistryToolTransport,
)
from ..agents.narrative_agent.models import NarrativeArtifact
from ..shared.image_generation import generate_seedream_image
from ..shared.models import StrictModel
from ..shared.settings import AssetGenerationSettings
from .dependencies import DependencyGraph, semantic_hash
from .dependency_builders import content_commits, has_content_graph
from .models import (
    ActionRecord,
    ArtifactRef,
    ProjectState,
    StatePatch,
    TaskBudget,
    TaskEnvelope,
    TaskScope,
)
from .production_routes import resolve_production_route
from .world_identities import (
    bound_identity_refs,
    document_from_state,
    identity_image_path,
    identity_prompt,
    identity_record,
    readable_reference,
    set_reference_image,
)

LIST_TOOL = "references.list_identities"
GENERATE_TOOL = "references.generate_identity"
SUBMIT_TOOL = "references.submit_candidate"


class IdentityImageProvider(Protocol):
    def generate(self, *, ref: str, prompt: str, output: Path) -> bytes: ...


class SeedreamIdentityImageProvider:
    def __init__(self, settings: AssetGenerationSettings) -> None:
        self.settings = settings
        import httpx

        self.client = httpx.Client()

    def __enter__(self) -> "SeedreamIdentityImageProvider":
        return self

    def __exit__(self, *_: object) -> None:
        self.client.close()

    def generate(self, *, ref: str, prompt: str, output: Path) -> bytes:
        del ref
        generated = generate_seedream_image(self.client, self.settings, prompt)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(generated.content)
        return generated.content


class IdentityReferenceItem(StrictModel):
    ref: str
    kind: str
    name: str
    local_path: str
    generation_prompt: str | None = None


class IdentityReferenceArtifact(StrictModel):
    schema_version: str = "identity-references.v1"
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    project_id: str
    identities: list[IdentityReferenceItem] = Field(min_length=1)


class IdentityReferenceRun(StrictModel):
    task: TaskEnvelope
    actions: list[ActionRecord]
    committed_state_version: int = Field(ge=1)
    project_state: ProjectState


class ShotReferenceHarnessController:
    """GenericAgent host: list missing identity stills, generate, submit."""

    def __init__(
        self,
        agent: GenericAgent,
        provider: IdentityImageProvider,
    ) -> None:
        self.agent = agent
        self.provider = provider

    @classmethod
    def from_provider(
        cls,
        provider: IdentityImageProvider,
        tool_model: object | None = None,
    ) -> "ShotReferenceHarnessController":
        from ..tools.registry import ToolRegistry

        tools = ToolRegistry()
        for name in (LIST_TOOL, GENERATE_TOOL, SUBMIT_TOOL):
            tools.register(name, lambda: None)

        def transport_factory(
            task: TaskEnvelope,
            kwargs: dict[str, Any],
        ) -> RegistryToolTransport:
            state: ProjectState = kwargs["state"]
            project_dir = Path(kwargs["project_dir"])
            session: dict[str, Any] = {"images": dict(_existing_images(state, project_dir))}

            def list_identities(problems: list[str] | None = None) -> dict[str, Any]:
                del problems
                return _list_payload(state, project_dir, session["images"])

            def generate_identity(
                ref: str,
                problems: list[str] | None = None,
            ) -> dict[str, Any]:
                del problems
                token = ref.strip()
                write_identity_image(state, project_dir, token, provider)
                relative = identity_image_path(project_dir, token).relative_to(
                    project_dir
                ).as_posix()
                session["images"][token] = relative
                return _list_payload(state, project_dir, session["images"])

            def submit(problems: list[str] | None = None) -> IdentityReferenceArtifact:
                del problems
                payload = _list_payload(state, project_dir, session["images"])
                if payload["pending"]:
                    raise RuntimeError(
                        "candidate is incomplete: missing identity images for "
                        + ", ".join(payload["pending"])
                    )
                items = [
                    IdentityReferenceItem(
                        ref=item["ref"],
                        kind=item["kind"],
                        name=item["name"],
                        local_path=item["path"],
                    )
                    for item in payload["identities"]
                    if item.get("path")
                ]
                if not items:
                    raise RuntimeError("no bound identities to submit")
                return IdentityReferenceArtifact(
                    project_id=state.context.project_id,
                    identities=items,
                )

            return RegistryToolTransport(
                [
                    RegistryTool(
                        name=LIST_TOOL,
                        description="列出被镜头绑定的身份，以及谁还缺参考图。",
                        handler=list_identities,
                    ),
                    RegistryTool(
                        name=GENERATE_TOOL,
                        description="为指定身份生成一张竖屏参考图，写回该节点。",
                        handler=generate_identity,
                        parameters={
                            "type": "object",
                            "properties": {
                                "ref": {"type": "string"},
                                "problems": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["ref"],
                            "additionalProperties": False,
                        },
                    ),
                    RegistryTool(
                        name=SUBMIT_TOOL,
                        description="范围内每条绑定身份都有图后提交。",
                        handler=submit,
                    ),
                ]
            )

        def context_builder(
            task: TaskEnvelope,
            kwargs: dict[str, Any],
        ) -> dict[str, Any]:
            state: ProjectState = kwargs["state"]
            return {
                "references_context": {
                    "project_id": state.context.project_id,
                    "identity_count": len(bound_identity_refs(state)),
                    "target_refs": list(task.scope.target_refs),
                }
            }

        return cls(
            GenericAgent(
                tool_model=tool_model or EnvironmentToolModel(),
                transport_factory=transport_factory,
                candidate_type=IdentityReferenceArtifact,
                completion_builder=_references_completion,
                context_builder=context_builder,
                capability_tools=tools,
            ),
            provider,
        )

    def create_task(self, narrative: NarrativeArtifact, state: ProjectState) -> TaskEnvelope:
        route = resolve_production_route(narrative)
        if route.format_id not in {"drama", "tutorial"}:
            raise ValueError("references stage only supports drama/tutorial")
        refs = bound_identity_refs(state)
        graph = DependencyGraph(state.dependency_graph)
        snapshot_refs = [ref for ref in refs if ref in state.dependency_graph.nodes]
        return TaskEnvelope(
            task_id=(
                f"task_references_{narrative.brief.project_id}_v"
                f"{state.context.state_version}"
            ),
            agent="references_agent",
            goal="为镜头绑定的身份各生成一张参考图，写回身份节点。",
            scope=TaskScope(
                project_id=narrative.brief.project_id,
                target_refs=refs,
            ),
            based_on_state_version=state.context.state_version,
            format_id=route.format_id,
            allowed_tools=[LIST_TOOL, GENERATE_TOOL, SUBMIT_TOOL],
            required_outputs=["identities"],
            forbidden_actions=["modify_shots", "generate_video"],
            acceptance_criteria=["每个被绑定的身份都有可读参考图"],
            budget=TaskBudget(
                max_steps=min(128, max(8, len(refs) * 2 + 6)),
                max_retries=1,
            ),
            input_hash=hashlib.sha256(
                narrative.model_dump_json().encode("utf-8")
            ).hexdigest(),
            dependency_snapshot=(
                graph.snapshot(snapshot_refs) if snapshot_refs else []
            ),
        )

    def run(
        self,
        narrative: NarrativeArtifact,
        project_dir: str | Path,
        state: ProjectState,
        task: TaskEnvelope | None = None,
    ) -> tuple[IdentityReferenceArtifact, IdentityReferenceRun]:
        route = resolve_production_route(narrative)
        if route.format_id not in {"drama", "tutorial"}:
            raise ValueError("references stage only supports drama/tutorial")
        if state.context.project_id != narrative.brief.project_id:
            raise ValueError("references inputs have different project_id values")
        if state.evaluation.stages.references not in {
            "ready",
            "pending",
            "blocked",
            "not_required",
            "needs_revision",
            "stale",
        }:
            raise ValueError("references stage is not ready in project state")
        if not has_content_graph(state.dependency_graph.nodes):
            raise ValueError("references stage requires a content graph")
        envelope = task or self.create_task(narrative, state)
        if envelope.based_on_state_version != state.context.state_version:
            raise ValueError("STALE_RESULT: references task uses an obsolete state")
        execution = self.agent.run(
            envelope,
            narrative=narrative,
            project_dir=project_dir,
            state=state,
        )
        artifact = execution.candidate
        if execution.result.status != "completed" or not isinstance(
            artifact, IdentityReferenceArtifact
        ):
            raise RuntimeError(
                execution.result.error or "references agent did not complete"
            )
        next_state = state.model_copy(deep=True)
        next_state.context.state_version += 1
        apply_identity_references(next_state, artifact)
        next_state.evaluation.stages.references = "passed"
        next_state.evaluation.stages.asset = "ready"
        run = IdentityReferenceRun(
            task=envelope,
            actions=execution.result.actions,
            committed_state_version=next_state.context.state_version,
            project_state=next_state,
        )
        return artifact, run


def write_identity_image(
    state: ProjectState,
    project_dir: str | Path,
    ref: str,
    provider: IdentityImageProvider,
    prompt: str | None = None,
) -> str:
    token = ref.strip()
    item = identity_record(state, token)
    if item is None:
        raise ValueError(f"production 里没有身份 {token}")
    root = Path(project_dir)
    output = identity_image_path(root, token)
    text = (prompt or "").strip() or identity_prompt(item)
    provider.generate(ref=token, prompt=text, output=output)
    if not output.is_file() or not output.stat().st_size:
        raise RuntimeError(f"identity image was not written: {output}")
    relative = output.relative_to(root).as_posix()
    set_reference_image(state, token, relative)
    item.generation_prompt = text
    return relative


def merge_identity_reference_artifact(
    existing: IdentityReferenceArtifact,
    incoming: IdentityReferenceArtifact,
) -> IdentityReferenceArtifact:
    by_ref = {item.ref: item for item in existing.identities}
    for item in incoming.identities:
        by_ref[item.ref] = item
    ordered: list[IdentityReferenceItem] = []
    seen: set[str] = set()
    for item in existing.identities:
        current = by_ref.get(item.ref)
        if current is None or item.ref in seen:
            continue
        ordered.append(current)
        seen.add(item.ref)
    for item in incoming.identities:
        if item.ref in seen:
            continue
        ordered.append(item)
        seen.add(item.ref)
    return incoming.model_copy(update={"identities": ordered})


def apply_identity_references(
    state: ProjectState,
    artifact: IdentityReferenceArtifact,
) -> None:
    refs = [item.ref for item in artifact.identities]
    for item in artifact.identities:
        set_reference_image(state, item.ref, item.local_path)
        record = identity_record(state, item.ref)
        if record is not None and item.generation_prompt:
            record.generation_prompt = item.generation_prompt
    document = document_from_state(state)
    allowed = set(refs)
    commits = [item for item in content_commits(document) if item.ref in allowed]
    missing = allowed - {item.ref for item in commits}
    if missing:
        raise ValueError(f"无法提交这些身份节点：{sorted(missing)}")
    nodes = state.dependency_graph.nodes
    changed = [
        item
        for item in commits
        if _identity_node_changed(nodes.get(item.ref), item.value)
    ]
    if not changed:
        return
    DependencyGraph(state.dependency_graph).commit_batch(
        task_id="references_submit",
        produced_by="asset_stage",
        commits=changed,
    )


def _identity_node_changed(node: object, value: object) -> bool:
    if node is None or getattr(node, "status", None) != "current":
        return True
    return getattr(node, "content_hash", None) != semantic_hash(value)


def _existing_images(state: ProjectState, project_dir: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for ref in bound_identity_refs(state):
        item = identity_record(state, ref)
        path = getattr(item, "reference_image", None) if item is not None else None
        if readable_reference(project_dir, path):
            found[ref] = str(path)
    return found


def _list_payload(
    state: ProjectState,
    project_dir: Path,
    images: dict[str, str],
) -> dict[str, Any]:
    identities: list[dict[str, Any]] = []
    pending: list[str] = []
    for ref in bound_identity_refs(state):
        item = identity_record(state, ref)
        name = str(getattr(item, "name", "") or ref) if item is not None else ref
        path = images.get(ref)
        if not readable_reference(project_dir, path):
            path = getattr(item, "reference_image", None) if item is not None else None
        has_image = readable_reference(project_dir, path)
        identities.append(
            {
                "ref": ref,
                "kind": ref.partition(":")[0],
                "name": name,
                "path": path if has_image else None,
                "has_image": has_image,
                "generation_prompt": (
                    getattr(item, "generation_prompt", None) if item is not None else None
                ),
            }
        )
        if not has_image:
            pending.append(ref)
    return {"identities": identities, "pending": pending}


def _references_completion(
    task: TaskEnvelope,
    kwargs: dict[str, Any],
) -> CompletionSpec:
    narrative = kwargs["narrative"]
    assert isinstance(narrative, NarrativeArtifact)
    return CompletionSpec(
        artifact_refs=[
            ArtifactRef(kind="identity_references", id=narrative.brief.project_id)
        ],
        state_patch=StatePatch(set={"video.references_status": "ready"}),
        evaluation_target=(
            f"references:{narrative.brief.project_id}@"
            f"{task.based_on_state_version + 1}"
        ),
    )
