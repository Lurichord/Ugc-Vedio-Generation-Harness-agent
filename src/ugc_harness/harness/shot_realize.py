"""Bind generated identity stills and shot videos onto existing graph nodes."""

from __future__ import annotations

from pathlib import Path

from .description_realization import apply_shot_media
from .models import ProjectState
from .shot_asset_controller import (
    ShotAssetArtifact,
    ShotAssetQuality,
    ShotVideoAsset,
    ShotVideoProvider,
    commit_shot_media,
    generate_shot_video,
)
from .shot_reference_controller import (
    IdentityReferenceArtifact,
    IdentityReferenceItem,
    IdentityImageProvider,
    apply_identity_references,
    write_identity_image,
)
from .world_identities import identity_record


def realize_identity_and_shots(
    project_dir: str | Path,
    state: ProjectState,
    *,
    identity_refs: list[str],
    shot_ids: list[str],
    image_provider: IdentityImageProvider | None = None,
    video_provider: ShotVideoProvider | None = None,
) -> None:
    """Rewrite identity.reference_image and shot.media for the circled refs."""

    root = Path(project_dir)
    if identity_refs:
        if image_provider is None:
            raise RuntimeError("重出身份参考图需要 image provider")
        items: list[IdentityReferenceItem] = []
        for ref in identity_refs:
            relative = write_identity_image(state, root, ref, image_provider)
            record = identity_record(state, ref)
            items.append(
                IdentityReferenceItem(
                    ref=ref,
                    kind=ref.partition(":")[0],
                    name=str(getattr(record, "name", "") or ref),
                    local_path=relative,
                )
            )
        apply_identity_references(
            state,
            IdentityReferenceArtifact(
                project_id=state.context.project_id,
                identities=items,
            ),
        )
    if not shot_ids:
        return
    if video_provider is None:
        raise RuntimeError("重出镜头视频需要 video provider")
    wanted = set(shot_ids)
    ordered = [
        shot.spec.shot_id
        for shot in sorted(state.production.shots, key=lambda item: item.spec.order)
        if shot.spec.shot_id in wanted
    ]
    assets: list[ShotVideoAsset] = []
    for shot_id in ordered:
        assets.append(
            generate_shot_video(
                state,
                root,
                shot_id,
                video_provider,
                replace_existing_job=True,
            )
        )
    artifact = ShotAssetArtifact(
        project_id=state.context.project_id,
        format_id=_format_id(state),
        assets=assets,
        quality=ShotAssetQuality(
            passed=True,
            shot_count=len(assets),
            ai_video_count=len(assets),
        ),
    )
    apply_shot_media(state, artifact)
    commit_shot_media(state, ordered)


def _format_id(state: ProjectState) -> str:
    kind = state.context.constraints.get("narrative_format") or state.context.constraints.get(
        "production_mode"
    )
    if kind in {"drama", "tutorial"}:
        return kind
    raise ValueError("realize_identity_and_shots only supports drama/tutorial")
