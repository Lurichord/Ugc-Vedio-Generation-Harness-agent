from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ugc_harness.harness.models import ProjectState

from .repository import AppDataRepository
from .schemas import ProjectSummary, StageName


STAGES: tuple[StageName, ...] = (
    "narrative", "voice", "editorial", "asset", "timeline", "render"
)
ARTIFACT_FILES: dict[StageName, str] = {
    "narrative": "narrative_artifact.json",
    "voice": "voice_artifact.json",
    "editorial": "editorial_artifact.json",
    "asset": "asset_artifact.json",
    "timeline": "timeline_artifact.json",
    "render": "render_artifact.json",
}
DIRECT_SHOT_ARTIFACT_FILES: dict[StageName, str] = {
    "asset": "shot_asset_artifact.json",
    "timeline": "shot_timeline_artifact.json",
}


class ProjectService:
    def __init__(self, output_root: Path, app_data: AppDataRepository) -> None:
        self.output_root = output_root.resolve()
        self.app_data = app_data

    def list_projects(self) -> list[ProjectSummary]:
        projects: list[ProjectSummary] = []
        if not self.output_root.is_dir():
            return projects
        for directory in self.output_root.iterdir():
            if not directory.is_dir() or not (directory / "manifest.json").is_file():
                continue
            try:
                manifest = self.read_json(directory, "manifest.json")
                state = self.read_state(directory)
            except (ValueError, OSError, json.JSONDecodeError):
                continue
            projects.append(
                ProjectSummary(
                    project_id=state.video.project_id,
                    project_name=str(manifest.get("project_name") or directory.name),
                    topic=manifest.get("topic"),
                    path_key=directory.name,
                    current_stage=self.current_stage(state, directory.name),
                    state_version=state.video.state_version,
                    updated_at=manifest.get("updated_at"),
                )
            )
        return sorted(projects, key=lambda item: item.updated_at or "", reverse=True)

    def project_dir(self, path_key: str) -> Path:
        candidate = (self.output_root / path_key).resolve()
        if candidate.parent != self.output_root or not candidate.is_dir():
            raise FileNotFoundError("project not found")
        return candidate

    def find_by_project_id(self, project_id: str) -> Path:
        for item in self.list_projects():
            if item.project_id == project_id:
                return self.project_dir(item.path_key)
        raise FileNotFoundError("project not found")

    @staticmethod
    def read_state(project_dir: Path) -> ProjectState:
        path = project_dir / "harness" / "project_state.json"
        return ProjectState.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def read_json(project_dir: Path, relative_path: str) -> dict[str, Any]:
        path = (project_dir / relative_path).resolve()
        if project_dir.resolve() not in path.parents:
            raise ValueError("invalid artifact path")
        return json.loads(path.read_text(encoding="utf-8"))

    def current_stage(self, state: ProjectState, storage_key: str) -> StageName:
        statuses = {
            stage: getattr(state.video, f"{stage}_status") for stage in STAGES
        }
        approvals = {
            item.stage: item for item in self.app_data.approvals(storage_key)
        }
        for stage in STAGES:
            if statuses[stage] == "not_required":
                continue
            if statuses[stage] not in {"passed", "stale"}:
                return stage
            approval = approvals.get(stage)
            if approval is None or any(
                (node := state.dependency_graph.nodes.get(ref)) is None
                or node.status != "current"
                or node.version != version
                for ref, version in approval.approved_refs.items()
            ):
                return stage
        return "render"

    @staticmethod
    def core_status(state: ProjectState, stage: StageName) -> str:
        return str(getattr(state.video, f"{stage}_status"))

    @staticmethod
    def stage_artifact(project_dir: Path, stage: StageName) -> dict[str, Any] | None:
        path = project_dir / ARTIFACT_FILES[stage]
        if not path.is_file() and stage in DIRECT_SHOT_ARTIFACT_FILES:
            path = project_dir / DIRECT_SHOT_ARTIFACT_FILES[stage]
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def evaluation(project_dir: Path, stage: StageName) -> dict[str, Any] | None:
        path = project_dir / "harness" / f"{stage}_evaluation.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
