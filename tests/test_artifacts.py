import json
from pathlib import Path

from ugc_harness.artifacts import ArtifactWriter, safe_project_folder_name
from ugc_harness.models import StageOneArtifact
from ugc_harness.pipeline import make_brief
from ugc_harness.quality import evaluate
from tests.test_quality import sample_plan, sample_script


def test_writer_creates_project_directory_with_all_artifacts(
    tmp_path: Path,
) -> None:
    brief = make_brief(
        topic="为什么测试很重要",
        project_name="测试项目",
        duration_seconds=90,
    )
    plan = sample_plan()
    script = sample_script(plan)
    artifact = StageOneArtifact(
        model="fake-model",
        brief=brief,
        planning=plan,
        script=script,
        quality=evaluate(brief, plan, script),
    )

    project_dir, written = ArtifactWriter(tmp_path).write(artifact)

    assert project_dir == tmp_path / "测试项目"
    assert {path.name for path in written} == {
        "01_creative_brief.json",
        "02_section_plan.json",
        "03_planned_beats.json",
        "04_content_plan.json",
        "05_script.json",
        "06_quality_report.json",
        "stage_one_artifact.json",
        "manifest.json",
    }
    manifest = json.loads((project_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["project_id"] == brief.project_id
    assert len(manifest["artifacts"]) == 7


def test_project_folder_name_removes_path_characters() -> None:
    assert safe_project_folder_name('项目: A/B?') == "项目_ A_B_"

