from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..agents.narrative_agent.models import NarrativeArtifact, PlanningArtifact
from ..harness.controller import NarrativeRunRecord
from ..agents.asset_agent.models import AssetArtifact
from ..harness.asset_controller import AssetRunRecord
from ..agents.timeline_agent.models import TimelineArtifact
from ..agents.render_agent.models import RenderArtifact
from ..agents.editorial_agent.models import EditorialArtifact
from ..agents.voice_agent.models import VoiceArtifact
from ..harness.editorial_controller import EditorialRunRecord
from ..harness.voice_controller import VoiceRunRecord
from ..harness.timeline_controller import TimelineRunRecord
from ..harness.render_controller import RenderRunRecord
from ..harness.shot_asset_controller import ShotAssetArtifact, ShotAssetRun
from ..harness.shot_timeline_controller import ShotTimelineArtifact, ShotTimelineRun
from ..harness.shot_render_controller import ShotRenderRun


_INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_project_folder_name(name: str) -> str:
    cleaned = _INVALID_PATH_CHARS.sub("_", name).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        raise ValueError("project name cannot be empty after path sanitization")
    return cleaned[:80]


def _json_text(value: BaseModel | dict[str, Any] | list[Any]) -> str:
    data = _jsonable(value)
    return json.dumps(data, ensure_ascii=False, indent=2)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


class ArtifactWriter:
    """Persist every final artifact under one project directory."""

    def __init__(self, output_root: str | Path = "outputs"):
        self.output_root = Path(output_root)

    def write(self, artifact: NarrativeArtifact) -> tuple[Path, list[Path]]:
        folder_name = safe_project_folder_name(
            artifact.brief.project_name
            or artifact.brief.topic
            or artifact.brief.project_id
        )
        project_dir = self.output_root / folder_name
        project_dir.mkdir(parents=True, exist_ok=True)

        payloads: list[tuple[str, BaseModel | dict[str, Any] | list[Any]]] = [
            ("01_creative_brief.json", artifact.brief),
        ]
        if isinstance(artifact.planning, PlanningArtifact):
            payloads.extend([
                (
                "02_section_plan.json",
                {
                    "project_id": artifact.brief.project_id,
                    "narrative_pattern": artifact.planning.narrative_pattern,
                    "one_sentence_thesis": artifact.planning.one_sentence_thesis,
                    "sections": [
                        section.model_dump(mode="json")
                        for section in artifact.planning.sections
                    ],
                },
                ),
                (
                "03_planned_beats.json",
                {
                    "project_id": artifact.brief.project_id,
                    "beats": [
                        beat.model_dump(mode="json")
                        for beat in artifact.planning.beats
                    ],
                },
                ),
                ("04_content_plan.json", artifact.planning),
            ])
        else:
            payloads.extend([
                ("02_world_state.json", artifact.planning.world_state),
                ("03_planning.json", artifact.planning),
            ])
        if artifact.script is not None:
            payloads.append(("05_script.json", artifact.script))
        if artifact.shots is not None:
            payloads.append(("05_shot_plan.json", artifact.shots))
        payloads.extend([
            ("06_quality_report.json", artifact.quality),
            ("narrative_artifact.json", artifact),
        ])

        written: list[Path] = []
        for filename, payload in payloads:
            path = _write_json(project_dir / filename, payload)
            written.append(path)

        manifest_path = project_dir / "manifest.json"
        manifest = {
            "schema_version": "artifact-manifest.v1",
            "project_id": artifact.brief.project_id,
            "project_name": artifact.brief.project_name or artifact.brief.topic,
            "topic": artifact.brief.topic,
            "stage": "narrative",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "artifacts": [
                {
                    "name": path.stem,
                    "file": path.name,
                    "relative_path": path.relative_to(project_dir).as_posix(),
                }
                for path in written
            ],
        }
        _write_json(manifest_path, manifest)
        written.append(manifest_path)
        return project_dir, written

    def write_narrative_run(
        self,
        project_dir: str | Path,
        record: NarrativeRunRecord,
    ) -> list[Path]:
        """Persist the harness contract separately from domain artifacts."""
        root = Path(project_dir)
        harness_dir = root / "harness"
        payloads: list[tuple[str, BaseModel]] = [
            ("narrative_task.json", record.task),
            ("narrative_agent_result.json", record.agent_result),
            ("narrative_evaluation.json", record.evaluation),
            ("narrative_transition.json", record.transition),
            ("project_state.json", record.project_state),
        ]
        written = [
            _write_json(harness_dir / filename, payload)
            for filename, payload in payloads
        ]
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        indexed = {
            entry["relative_path"]: entry
            for entry in manifest.get("artifacts", [])
        }
        indexed.update(
            {
                path.relative_to(root).as_posix(): _manifest_entry(
                    path, root, "harness"
                )
                for path in written
            }
        )
        manifest["state_version"] = record.committed_state_version
        manifest["stage"] = "narrative_agent_complete"
        manifest["artifacts"] = list(indexed.values())
        _write_json(manifest_path, manifest)
        return [*written, manifest_path]

    def write_voice(
        self,
        project_dir: str | Path,
        artifact: VoiceArtifact,
    ) -> list[Path]:
        root = Path(project_dir)
        root.mkdir(parents=True, exist_ok=True)
        payloads: list[tuple[str, BaseModel | dict[str, Any] | list[Any]]] = [
            ("07_voice_plan.json", artifact.voice_plan),
            ("08_timed_audio.json", artifact.timed_audio),
            ("09_word_alignment.json", artifact.word_alignment),
            (
                "10_realized_beats.json",
                {
                    "project_id": artifact.project_id,
                    "beats": [
                        beat.model_dump(mode="json")
                        for beat in artifact.realized_beats
                    ],
                },
            ),
            ("11_voice_quality_report.json", artifact.quality),
            ("voice_artifact.json", artifact),
        ]
        written = [
            _write_json(root / filename, payload)
            for filename, payload in payloads
        ]

        manifest_path = root / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        else:
            manifest = {
                "schema_version": "artifact-manifest.v1",
                "project_id": artifact.project_id,
                "project_name": root.name,
                "topic": None,
                "artifacts": [],
            }
        manifest["stage"] = "voice_agent_complete"
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        manifest["voice_quality_passed"] = artifact.quality.passed

        new_entries = [
            _manifest_entry(path, root, "json") for path in written
        ]
        narration_path = root / artifact.timed_audio.audio_file
        new_entries.append(_manifest_entry(narration_path, root, "audio"))
        for segment in artifact.timed_audio.segments:
            new_entries.append(
                _manifest_entry(root / segment.file, root, "audio_segment")
            )
        indexed = {
            entry["relative_path"]: entry
            for entry in manifest.get("artifacts", [])
        }
        indexed.update(
            {entry["relative_path"]: entry for entry in new_entries}
        )
        manifest["artifacts"] = list(indexed.values())
        _write_json(manifest_path, manifest)
        written.append(manifest_path)
        return written

    def write_voice_run(
        self,
        project_dir: str | Path,
        record: VoiceRunRecord,
    ) -> list[Path]:
        root = Path(project_dir)
        harness_dir = root / "harness"
        payloads: list[tuple[str, BaseModel]] = [
            ("voice_task.json", record.task),
            ("voice_agent_result.json", record.agent_result),
            ("voice_evaluation.json", record.evaluation),
            ("voice_transition.json", record.transition),
            ("project_state.json", record.project_state),
        ]
        written = [
            _write_json(harness_dir / filename, payload)
            for filename, payload in payloads
        ]
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        indexed = {
            entry["relative_path"]: entry
            for entry in manifest.get("artifacts", [])
        }
        indexed.update(
            {
                path.relative_to(root).as_posix(): _manifest_entry(
                    path, root, "harness"
                )
                for path in written
            }
        )
        manifest["state_version"] = record.committed_state_version
        manifest["artifacts"] = list(indexed.values())
        _write_json(manifest_path, manifest)
        return [*written, manifest_path]

    def write_editorial(
        self,
        project_dir: str | Path,
        artifact: EditorialArtifact,
    ) -> list[Path]:
        root = Path(project_dir)
        root.mkdir(parents=True, exist_ok=True)
        plan = artifact.editorial_plan
        payloads: list[tuple[str, BaseModel | dict[str, Any] | list[Any]]] = [
            (
                "12_claim_map.json",
                {
                    "project_id": artifact.project_id,
                    "claims": [
                        claim.model_dump(mode="json") for claim in plan.claims
                    ],
                },
            ),
            (
                "13_visual_requirements.json",
                {
                    "project_id": artifact.project_id,
                    "requirements": [
                        requirement.model_dump(mode="json")
                        for requirement in plan.visual_requirements
                    ],
                },
            ),
            ("14_editorial_quality_report.json", artifact.quality),
            ("editorial_artifact.json", artifact),
        ]
        written = [
            _write_json(root / filename, payload)
            for filename, payload in payloads
        ]

        manifest_path = root / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        else:
            manifest = {
                "schema_version": "artifact-manifest.v1",
                "project_id": artifact.project_id,
                "project_name": root.name,
                "topic": None,
                "artifacts": [],
            }
        manifest["stage"] = "editorial_agent_complete"
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        manifest["editorial_quality_passed"] = artifact.quality.passed
        indexed = {
            entry["relative_path"]: entry
            for entry in manifest.get("artifacts", [])
        }
        obsolete = {
            "12_claim_evidence_map.json",
            "13_research_queries.json",
            "14_visual_requirements.json",
            "15_editorial_quality_report.json",
        }
        for filename in obsolete:
            stale_path = root / filename
            if stale_path.is_file():
                stale_path.unlink()
            indexed.pop(filename, None)
        indexed.update(
            {
                path.relative_to(root).as_posix(): _manifest_entry(
                    path, root, "json"
                )
                for path in written
            }
        )
        manifest["artifacts"] = list(indexed.values())
        _write_json(manifest_path, manifest)
        written.append(manifest_path)
        return written

    def write_editorial_run(
        self,
        project_dir: str | Path,
        record: EditorialRunRecord,
    ) -> list[Path]:
        root = Path(project_dir)
        harness_dir = root / "harness"
        payloads: list[tuple[str, BaseModel]] = [
            ("editorial_task.json", record.task),
            ("editorial_agent_result.json", record.agent_result),
            ("editorial_evaluation.json", record.evaluation),
            ("editorial_transition.json", record.transition),
            ("project_state.json", record.project_state),
        ]
        written = [
            _write_json(harness_dir / filename, payload)
            for filename, payload in payloads
        ]
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        indexed = {
            entry["relative_path"]: entry
            for entry in manifest.get("artifacts", [])
        }
        indexed.update(
            {
                path.relative_to(root).as_posix(): _manifest_entry(
                    path, root, "harness"
                )
                for path in written
            }
        )
        manifest["state_version"] = record.committed_state_version
        manifest["stage"] = "editorial_agent_complete"
        manifest["artifacts"] = list(indexed.values())
        _write_json(manifest_path, manifest)
        return [*written, manifest_path]

    def write_assets(
        self,
        project_dir: str | Path,
        artifact: AssetArtifact,
    ) -> list[Path]:
        root = Path(project_dir)
        root.mkdir(parents=True, exist_ok=True)
        payloads: list[tuple[str, BaseModel | dict[str, Any] | list[Any]]] = [
            (
                "15_asset_cards.json",
                {
                    "project_id": artifact.project_id,
                    "assets": [
                        asset.model_dump(mode="json")
                        for asset in artifact.assets
                    ],
                },
            ),
            (
                "16_visual_resolutions.json",
                {
                    "project_id": artifact.project_id,
                    "resolutions": [
                        resolution.model_dump(mode="json")
                        for resolution in artifact.resolutions
                    ],
                },
            ),
            (
                "asset_inspections.json",
                {
                    "project_id": artifact.project_id,
                    "inspections": [
                        item.model_dump(mode="json")
                        for item in artifact.inspections
                    ],
                },
            ),
            (
                "prepared_images.json",
                {
                    "project_id": artifact.project_id,
                    "prepared_images": [
                        item.model_dump(mode="json")
                        for item in artifact.prepared_images
                    ],
                },
            ),
            ("17_asset_quality_report.json", artifact.quality),
            ("asset_artifact.json", artifact),
        ]
        written = [
            _write_json(root / filename, payload)
            for filename, payload in payloads
        ]

        manifest_path = root / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        else:
            manifest = {
                "schema_version": "artifact-manifest.v1",
                "project_id": artifact.project_id,
                "project_name": root.name,
                "topic": None,
                "artifacts": [],
            }
        manifest["stage"] = "asset_agent_complete"
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        manifest["asset_quality_passed"] = artifact.quality.passed
        indexed = {
            entry["relative_path"]: entry
            for entry in manifest.get("artifacts", [])
        }
        indexed.update(
            {
                path.relative_to(root).as_posix(): _manifest_entry(
                    path, root, "json"
                )
                for path in written
            }
        )
        for asset in artifact.assets:
            asset_path = root / asset.local_path
            indexed[asset.local_path] = _manifest_entry(
                asset_path, root, "asset"
            )
        for image in artifact.prepared_images:
            image_path = root / image.output_path
            indexed[image.output_path] = _manifest_entry(
                image_path, root, "prepared_image"
            )
        manifest["artifacts"] = list(indexed.values())
        _write_json(manifest_path, manifest)
        written.append(manifest_path)
        return written

    def write_shot_assets(
        self,
        project_dir: str | Path,
        artifact: ShotAssetArtifact,
    ) -> list[Path]:
        root = Path(project_dir)
        written = [
            _write_json(root / "shot_asset_artifact.json", artifact),
        ]
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["stage"] = "shot_asset_complete"
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        manifest["asset_quality_passed"] = artifact.quality.passed
        indexed = {
            entry["relative_path"]: entry
            for entry in manifest.get("artifacts", [])
        }
        indexed["shot_asset_artifact.json"] = _manifest_entry(
            written[0], root, "json"
        )
        for asset in artifact.assets:
            indexed[asset.local_path] = _manifest_entry(
                root / asset.local_path, root, "ai_video"
            )
        manifest["artifacts"] = list(indexed.values())
        _write_json(manifest_path, manifest)
        return [*written, manifest_path]

    def write_shot_asset_run(
        self,
        project_dir: str | Path,
        record: ShotAssetRun,
    ) -> list[Path]:
        root = Path(project_dir)
        harness_dir = root / "harness"
        written = [
            _write_json(harness_dir / "shot_asset_task.json", record.task),
            _write_json(harness_dir / "shot_asset_actions.json", record.actions),
            _write_json(harness_dir / "project_state.json", record.project_state),
        ]
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        indexed = {
            entry["relative_path"]: entry
            for entry in manifest.get("artifacts", [])
        }
        indexed.update({
            path.relative_to(root).as_posix(): _manifest_entry(path, root, "harness")
            for path in written
        })
        manifest["state_version"] = record.committed_state_version
        manifest["artifacts"] = list(indexed.values())
        _write_json(manifest_path, manifest)
        return [*written, manifest_path]

    def write_asset_run(
        self,
        project_dir: str | Path,
        record: AssetRunRecord,
    ) -> list[Path]:
        root = Path(project_dir)
        harness_dir = root / "harness"
        payloads: list[tuple[str, BaseModel]] = [
            ("asset_task.json", record.task),
            ("asset_agent_result.json", record.agent_result),
            ("asset_evaluation.json", record.evaluation),
            ("asset_transition.json", record.transition),
            ("project_state.json", record.project_state),
        ]
        written = [
            _write_json(harness_dir / filename, payload)
            for filename, payload in payloads
        ]
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        indexed = {
            entry["relative_path"]: entry
            for entry in manifest.get("artifacts", [])
        }
        indexed.update(
            {
                path.relative_to(root).as_posix(): _manifest_entry(
                    path, root, "harness"
                )
                for path in written
            }
        )
        manifest["state_version"] = record.committed_state_version
        manifest["stage"] = "asset_agent_complete"
        manifest["artifacts"] = list(indexed.values())
        _write_json(manifest_path, manifest)
        return [*written, manifest_path]

    def write_timeline(
        self,
        project_dir: str | Path,
        artifact: TimelineArtifact,
    ) -> list[Path]:
        root = Path(project_dir)
        root.mkdir(parents=True, exist_ok=True)
        payloads: list[tuple[str, BaseModel | dict[str, Any] | list[Any]]] = [
            ("18_timeline_plan.json", artifact.timeline),
            (
                "19_caption_plan.json",
                {
                    "project_id": artifact.project_id,
                    "captions": [
                        item.model_dump(mode="json")
                        for item in artifact.captions
                    ],
                },
            ),
            (
                "20_visual_transform_plan.json",
                {
                    "project_id": artifact.project_id,
                    "transforms": [
                        item.model_dump(mode="json")
                        for item in artifact.visual_transforms
                    ],
                },
            ),
            (
                "21_overlay_plan.json",
                {
                    "project_id": artifact.project_id,
                    "overlays": [
                        item.model_dump(mode="json")
                        for item in artifact.overlays
                    ],
                },
            ),
            ("22_timeline_quality_report.json", artifact.quality),
            ("timeline_artifact.json", artifact),
        ]
        written = [
            _write_json(root / filename, payload)
            for filename, payload in payloads
        ]

        manifest_path = root / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        else:
            manifest = {
                "schema_version": "artifact-manifest.v1",
                "project_id": artifact.project_id,
                "project_name": root.name,
                "topic": None,
                "artifacts": [],
            }
        manifest["stage"] = "timeline_agent_complete"
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        manifest["timeline_quality_passed"] = artifact.quality.passed
        indexed = {
            entry["relative_path"]: entry
            for entry in manifest.get("artifacts", [])
        }
        indexed.update(
            {
                path.relative_to(root).as_posix(): _manifest_entry(
                    path, root, "json"
                )
                for path in written
            }
        )
        for derivative in artifact.derivatives:
            path = root / derivative.local_path
            indexed[derivative.local_path] = _manifest_entry(
                path, root, "derived_asset"
            )
        manifest["artifacts"] = list(indexed.values())
        _write_json(manifest_path, manifest)
        written.append(manifest_path)
        return written

    def write_timeline_run(
        self,
        project_dir: str | Path,
        record: TimelineRunRecord,
    ) -> list[Path]:
        return self._write_agent_run(project_dir, "timeline", record)

    def write_shot_timeline(
        self,
        project_dir: str | Path,
        artifact: ShotTimelineArtifact,
    ) -> list[Path]:
        root = Path(project_dir)
        path = _write_json(root / "shot_timeline_artifact.json", artifact)
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        indexed = {
            entry["relative_path"]: entry
            for entry in manifest.get("artifacts", [])
        }
        indexed[path.name] = _manifest_entry(path, root, "json")
        manifest["stage"] = "shot_timeline_complete"
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        manifest["timeline_quality_passed"] = True
        manifest["artifacts"] = list(indexed.values())
        _write_json(manifest_path, manifest)
        return [path, manifest_path]

    def write_shot_timeline_run(
        self,
        project_dir: str | Path,
        record: ShotTimelineRun,
    ) -> list[Path]:
        root = Path(project_dir)
        harness_dir = root / "harness"
        written = [
            _write_json(harness_dir / "shot_timeline_task.json", record.task),
            _write_json(harness_dir / "shot_timeline_actions.json", record.actions),
            _write_json(harness_dir / "project_state.json", record.project_state),
        ]
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        indexed = {
            entry["relative_path"]: entry
            for entry in manifest.get("artifacts", [])
        }
        indexed.update({
            path.relative_to(root).as_posix(): _manifest_entry(path, root, "harness")
            for path in written
        })
        manifest["state_version"] = record.committed_state_version
        manifest["artifacts"] = list(indexed.values())
        _write_json(manifest_path, manifest)
        return [*written, manifest_path]

    def write_render(
        self,
        project_dir: str | Path,
        artifact: RenderArtifact,
    ) -> list[Path]:
        root = Path(project_dir)
        root.mkdir(parents=True, exist_ok=True)
        payloads: list[tuple[str, BaseModel | dict[str, Any] | list[Any]]] = [
            ("27_render_composition.json", artifact.composition),
            ("28_render_quality_report.json", artifact.quality),
            ("render_artifact.json", artifact),
        ]
        written = [
            _write_json(root / filename, payload)
            for filename, payload in payloads
        ]
        manifest_path = root / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        else:
            manifest = {
                "schema_version": "artifact-manifest.v1",
                "project_id": artifact.project_id,
                "project_name": root.name,
                "topic": None,
                "artifacts": [],
            }
        manifest["stage"] = "final_render_complete"
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        manifest["render_quality_passed"] = artifact.quality.passed
        indexed = {
            entry["relative_path"]: entry
            for entry in manifest.get("artifacts", [])
        }
        indexed.update(
            {
                path.relative_to(root).as_posix(): _manifest_entry(
                    path, root, "json"
                )
                for path in written
            }
        )
        for output in artifact.outputs:
            path = root / output.local_path
            indexed[output.local_path] = _manifest_entry(
                path, root, f"{output.kind}_video"
            )
        manifest["artifacts"] = list(indexed.values())
        _write_json(manifest_path, manifest)
        written.append(manifest_path)
        return written

    def write_render_run(
        self,
        project_dir: str | Path,
        record: RenderRunRecord,
    ) -> list[Path]:
        return self._write_agent_run(project_dir, "render", record)

    def write_shot_render_run(
        self,
        project_dir: str | Path,
        record: ShotRenderRun,
    ) -> list[Path]:
        root = Path(project_dir)
        harness_dir = root / "harness"
        written = [
            _write_json(harness_dir / "shot_render_task.json", record.task),
            _write_json(harness_dir / "shot_render_actions.json", record.actions),
            _write_json(harness_dir / "project_state.json", record.project_state),
        ]
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        indexed = {
            entry["relative_path"]: entry
            for entry in manifest.get("artifacts", [])
        }
        indexed.update({
            path.relative_to(root).as_posix(): _manifest_entry(path, root, "harness")
            for path in written
        })
        manifest["state_version"] = record.committed_state_version
        manifest["artifacts"] = list(indexed.values())
        _write_json(manifest_path, manifest)
        return [*written, manifest_path]

    def _write_agent_run(
        self,
        project_dir: str | Path,
        phase: str,
        record: TimelineRunRecord | RenderRunRecord,
    ) -> list[Path]:
        root = Path(project_dir)
        harness_dir = root / "harness"
        payloads: list[tuple[str, BaseModel]] = [
            (f"{phase}_task.json", record.task),
            (f"{phase}_agent_result.json", record.agent_result),
            (f"{phase}_evaluation.json", record.evaluation),
            (f"{phase}_transition.json", record.transition),
            ("project_state.json", record.project_state),
        ]
        written = [
            _write_json(harness_dir / filename, payload)
            for filename, payload in payloads
        ]
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        indexed = {
            entry["relative_path"]: entry
            for entry in manifest.get("artifacts", [])
        }
        indexed.update(
            {
                path.relative_to(root).as_posix(): _manifest_entry(
                    path, root, "harness"
                )
                for path in written
            }
        )
        manifest["state_version"] = record.committed_state_version
        manifest["stage"] = f"{phase}_agent_complete"
        manifest["artifacts"] = list(indexed.values())
        _write_json(manifest_path, manifest)
        return [*written, manifest_path]


def _write_json(
    path: Path,
    payload: BaseModel | dict[str, Any] | list[Any],
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(_json_text(payload), encoding="utf-8")
    temp_path.replace(path)
    return path


def _manifest_entry(path: Path, root: Path, kind: str) -> dict[str, str]:
    return {
        "name": path.stem,
        "file": path.name,
        "relative_path": path.relative_to(root).as_posix(),
        "kind": kind,
    }
