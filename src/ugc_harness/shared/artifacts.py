from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..stage_one.models import StageOneArtifact
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
                "12_claim_evidence_map.json",
                {
                    "project_id": artifact.project_id,
                    "claims": [
                        claim.model_dump(mode="json") for claim in plan.claims
                    ],
                    "evidence_requests": [
                        request.model_dump(mode="json")
                        for request in plan.evidence_requests
                    ],
                },
            ),
            (
                "13_research_queries.json",
                {
                    "project_id": artifact.project_id,
                    "requests": [
                        request.model_dump(mode="json")
                        for request in plan.evidence_requests
                    ],
                },
            ),
            (
                "14_visual_requirements.json",
                {
                    "project_id": artifact.project_id,
                    "requirements": [
                        requirement.model_dump(mode="json")
                        for requirement in plan.visual_requirements
                    ],
                },
            ),
            ("15_editorial_quality_report.json", artifact.quality),
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
