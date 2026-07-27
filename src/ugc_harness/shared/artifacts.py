from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..stage_one.models import StageOneArtifact
from ..stage_four.models import AssetStageArtifact
from ..stage_five.models import TimelineStageArtifact
from ..stage_seven.models import ImagePreparationStageArtifact
from ..stage_six.models import RenderStageArtifact
from ..stage_three.models import EditorialStageArtifact
from ..stage_two.models import VoiceStageArtifact


_INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_project_folder_name(name: str) -> str:
    cleaned = _INVALID_PATH_CHARS.sub("_", name).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        raise ValueError("project name cannot be empty after path sanitization")
    return cleaned[:80]


def _json_text(value: BaseModel | dict[str, Any] | list[Any]) -> str:
    if isinstance(value, BaseModel):
        data = value.model_dump(mode="json")
    else:
        data = value
    return json.dumps(data, ensure_ascii=False, indent=2)


class ArtifactWriter:
    """Persist every final artifact under one project directory."""

    def __init__(self, output_root: str | Path = "outputs"):
        self.output_root = Path(output_root)

    def write(self, artifact: StageOneArtifact) -> tuple[Path, list[Path]]:
        folder_name = safe_project_folder_name(
            artifact.brief.project_name
            or artifact.brief.topic
            or artifact.brief.project_id
        )
        project_dir = self.output_root / folder_name
        project_dir.mkdir(parents=True, exist_ok=True)

        payloads: list[tuple[str, BaseModel | dict[str, Any] | list[Any]]] = [
            ("01_creative_brief.json", artifact.brief),
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
            ("05_script.json", artifact.script),
            ("06_quality_report.json", artifact.quality),
            ("stage_one_artifact.json", artifact),
        ]

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
            "stage": "stage_one",
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

    def write_voice_stage(
        self,
        project_dir: str | Path,
        artifact: VoiceStageArtifact,
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
            ("stage_two_artifact.json", artifact),
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
        manifest["stage"] = "voice_complete"
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

    def write_editorial_stage(
        self,
        project_dir: str | Path,
        artifact: EditorialStageArtifact,
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
            ("stage_three_artifact.json", artifact),
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
        manifest["stage"] = "editorial_plan_complete"
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

    def write_asset_stage(
        self,
        project_dir: str | Path,
        artifact: AssetStageArtifact,
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
            ("17_asset_quality_report.json", artifact.quality),
            ("stage_four_artifact.json", artifact),
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
        manifest["stage"] = "asset_acquisition_complete"
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
        manifest["artifacts"] = list(indexed.values())
        _write_json(manifest_path, manifest)
        written.append(manifest_path)
        return written

    def write_timeline_stage(
        self,
        project_dir: str | Path,
        artifact: TimelineStageArtifact,
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
            ("stage_five_artifact.json", artifact),
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
        manifest["stage"] = "timeline_composition_complete"
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

    def write_image_preparation_stage(
        self,
        project_dir: str | Path,
        artifact: ImagePreparationStageArtifact,
    ) -> list[Path]:
        root = Path(project_dir)
        root.mkdir(parents=True, exist_ok=True)
        payloads: list[tuple[str, BaseModel | dict[str, Any] | list[Any]]] = [
            (
                "23_image_analysis.json",
                {
                    "project_id": artifact.project_id,
                    "analyses": [
                        item.analysis.model_dump(mode="json")
                        for item in artifact.processed_images
                    ],
                },
            ),
            (
                "24_processed_images.json",
                {
                    "project_id": artifact.project_id,
                    "images": [
                        item.model_dump(mode="json")
                        for item in artifact.processed_images
                    ],
                },
            ),
            (
                "25_render_asset_map.json",
                {
                    "project_id": artifact.project_id,
                    "mappings": [
                        item.model_dump(mode="json")
                        for item in artifact.render_asset_mappings
                    ],
                },
            ),
            ("26_image_quality_report.json", artifact.quality),
            ("stage_seven_artifact.json", artifact),
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
        manifest["stage"] = "image_preparation_complete"
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        manifest["image_quality_passed"] = artifact.quality.passed
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
        for image in artifact.processed_images:
            path = root / image.output_path
            indexed[image.output_path] = _manifest_entry(
                path, root, "processed_image"
            )
        manifest["artifacts"] = list(indexed.values())
        _write_json(manifest_path, manifest)
        written.append(manifest_path)
        return written

    def write_render_stage(
        self,
        project_dir: str | Path,
        artifact: RenderStageArtifact,
    ) -> list[Path]:
        root = Path(project_dir)
        root.mkdir(parents=True, exist_ok=True)
        payloads: list[tuple[str, BaseModel | dict[str, Any] | list[Any]]] = [
            ("27_render_composition.json", artifact.composition),
            ("28_render_quality_report.json", artifact.quality),
            ("stage_six_artifact.json", artifact),
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
