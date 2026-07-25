from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .models import StageOneArtifact


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
    """Persist every final stage-one artifact under one project directory."""

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
            path = project_dir / filename
            temp_path = path.with_suffix(path.suffix + ".tmp")
            temp_path.write_text(_json_text(payload), encoding="utf-8")
            temp_path.replace(path)
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
        temp_manifest = manifest_path.with_suffix(".json.tmp")
        temp_manifest.write_text(_json_text(manifest), encoding="utf-8")
        temp_manifest.replace(manifest_path)
        written.append(manifest_path)
        return project_dir, written
