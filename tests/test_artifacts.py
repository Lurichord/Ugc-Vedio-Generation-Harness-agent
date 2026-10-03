import json
from pathlib import Path

from ugc_harness.shared.artifacts import (
    ArtifactWriter,
    folder_token,
    project_folder_name,
    safe_project_folder_name,
)
from ugc_harness.harness.shot_reference_controller import (
    IdentityReferenceArtifact,
    IdentityReferenceItem,
    merge_identity_reference_artifact,
)
from ugc_harness.agents.narrative_agent import NarrativeArtifact, make_brief
from ugc_harness.agents.narrative_agent.brief import new_project_id
from ugc_harness.agents.narrative_agent.quality import evaluate
from ugc_harness.content import CommunicationSpec
from ugc_harness.intake.models import BriefDraft
from ugc_harness.intake.view import materialize_brief
from tests.test_quality import sample_plan, sample_script


def _artifact(project_name: str = "测试项目"):
    brief = make_brief(
        topic="为什么测试很重要",
        project_name=project_name,
        duration_seconds=90,
    )
    plan = sample_plan()
    script = sample_script(plan)
    return NarrativeArtifact(
        model="fake-model",
        brief=brief,
        planning=plan,
        script=script,
        quality=evaluate(brief, plan, script),
    )


def test_writer_creates_project_directory_with_all_artifacts(
    tmp_path: Path,
) -> None:
    artifact = _artifact()
    project_dir, written = ArtifactWriter(tmp_path).write(artifact)
    token = folder_token(artifact.brief.project_id)
    assert project_dir == tmp_path / f"测试项目_{token}"
    assert {path.name for path in written} == {
        "01_creative_brief.json",
        "02_section_plan.json",
        "03_planned_beats.json",
        "04_content_plan.json",
        "05_script.json",
        "06_quality_report.json",
        "narrative_artifact.json",
        "manifest.json",
    }
    manifest = json.loads((project_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["project_id"] == artifact.brief.project_id
    assert len(manifest["artifacts"]) == 7


def test_writer_does_not_reuse_an_existing_same_name_folder(tmp_path: Path) -> None:
    artifact = _artifact("中秋节")
    first_dir, _ = ArtifactWriter(tmp_path).write(artifact)
    second_dir, _ = ArtifactWriter(tmp_path).write(artifact)
    assert first_dir != second_dir
    assert first_dir.is_dir() and second_dir.is_dir()
    assert first_dir.name.startswith("中秋节_")
    assert second_dir.name.startswith("中秋节_")


def test_project_folder_name_includes_id_hash() -> None:
    assert project_folder_name("中秋节", "ugc_topic_92ba816f") == "中秋节_92ba816f"
    assert safe_project_folder_name('项目: A/B?') == "项目_ A_B_"


def test_identity_reference_write_merges_existing_identities(tmp_path: Path) -> None:
    artifact = _artifact()
    project_dir, _ = ArtifactWriter(tmp_path).write(artifact)
    writer = ArtifactWriter(tmp_path)
    writer.write_identity_references(
        project_dir,
        IdentityReferenceArtifact(
            project_id=artifact.brief.project_id,
            identities=[
                IdentityReferenceItem(
                    ref="cast:houyi",
                    kind="cast",
                    name="后羿",
                    local_path="assets/identity_reference/cast_houyi.png",
                ),
                IdentityReferenceItem(
                    ref="cast:change",
                    kind="cast",
                    name="嫦娥",
                    local_path="assets/identity_reference/cast_change.png",
                ),
            ],
        ),
    )
    writer.write_identity_references(
        project_dir,
        IdentityReferenceArtifact(
            project_id=artifact.brief.project_id,
            identities=[
                IdentityReferenceItem(
                    ref="cast:change",
                    kind="cast",
                    name="嫦娥",
                    local_path="assets/identity_reference/cast_change.png",
                    generation_prompt="9:16 竖屏。嫦娥改过一版。",
                ),
            ],
        ),
    )
    payload = json.loads(
        (project_dir / "identity_reference_artifact.json").read_text(encoding="utf-8")
    )
    refs = [item["ref"] for item in payload["identities"]]
    assert refs == ["cast:houyi", "cast:change"]
    change = next(item for item in payload["identities"] if item["ref"] == "cast:change")
    assert change["generation_prompt"] == "9:16 竖屏。嫦娥改过一版。"


def test_merge_identity_reference_keeps_unedited_order() -> None:
    existing = IdentityReferenceArtifact(
        project_id="p1",
        identities=[
            IdentityReferenceItem(
                ref="cast:a", kind="cast", name="甲", local_path="a.png"
            ),
            IdentityReferenceItem(
                ref="prop:b", kind="prop", name="乙", local_path="b.png"
            ),
        ],
    )
    incoming = IdentityReferenceArtifact(
        project_id="p1",
        identities=[
            IdentityReferenceItem(
                ref="prop:b",
                kind="prop",
                name="乙",
                local_path="b2.png",
                generation_prompt="改过",
            ),
            IdentityReferenceItem(
                ref="place:c", kind="place", name="丙", local_path="c.png"
            ),
        ],
    )
    merged = merge_identity_reference_artifact(existing, incoming)
    assert [item.ref for item in merged.identities] == ["cast:a", "prop:b", "place:c"]
    assert merged.identities[1].local_path == "b2.png"
    assert merged.identities[0].local_path == "a.png"


def test_materialize_assigns_a_unique_project_id() -> None:
    draft = BriefDraft(
        topic="中秋节",
        production_mode="drama",
        communication=CommunicationSpec(goal="让观众了解屈原"),
    )
    first = materialize_brief(draft)
    second = materialize_brief(draft)
    assert first.project_id != second.project_id
    assert new_project_id("中秋节") != new_project_id("中秋节")
