from __future__ import annotations

import hashlib
from pathlib import Path
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .image_models import AssetInspection, ImageStrategy, PreparedImage
from .models import AssetCard


class ImagePreparationCapabilities:
    def prepare_image(
        self,
        *,
        asset: AssetCard,
        inspection: AssetInspection,
        project_dir: str | Path,
    ) -> PreparedImage:
        if inspection.blocking_overlay:
            raise ValueError("blocking overlays cannot be repaired by cropping")
        root = Path(project_dir)
        source = root / asset.local_path
        if not source.is_file():
            raise ValueError(f"source image is missing: {asset.local_path}")
        strategy = _select_strategy(asset, inspection)
        output_dir = root / "assets" / "prepared_image"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"prepared_{asset.asset_id}.jpg"
        input_width, input_height = _render_image(
            source,
            output,
            strategy,
            inspection.focal_box,
        )
        raw = output.read_bytes()
        return PreparedImage(
            processed_id=f"prepared_{asset.asset_id}",
            asset_id=asset.asset_id,
            beat_id=asset.beat_id,
            source_path=asset.local_path,
            output_path=output.relative_to(root).as_posix(),
            sha256=hashlib.sha256(raw).hexdigest(),
            input_width=input_width,
            input_height=input_height,
            strategy=strategy,
            upscaled=input_width < 1080 or input_height < 1920,
            analysis=inspection,
        )


def _select_strategy(
    asset: AssetCard,
    analysis: AssetInspection,
) -> ImageStrategy:
    # Generated images are presentation-ready visuals, not source material that
    # needs a smaller foreground panel. Always render them as one full-frame
    # layer, even when the analyzer recommends preserving the whole image.
    if asset.origin == "generated" and asset.modality == "ai_image":
        return "subject_cover"
    if asset.modality in {
        "source_screenshot",
        "document_screenshot",
        "chart",
    }:
        focal_area = analysis.focal_box[2] * analysis.focal_box[3]
        if (
            analysis.focus_confidence >= 0.6
            and (not analysis.preserve_full_frame or focal_area < 0.12)
        ):
            return "focus_crop"
        return "contained_background"
    if asset.modality in {"real_image", "meme"}:
        return "subject_cover"
    if analysis.preserve_full_frame:
        return "contained_background"
    return "portrait_normalize"


def _render_image(
    source: Path,
    output: Path,
    strategy: ImageStrategy,
    focal_box: tuple[float, float, float, float],
) -> tuple[int, int]:
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        input_size = image.size
        if strategy == "contained_background":
            rendered = _contained_background(image)
        elif strategy == "focus_crop":
            rendered = _focus_crop(image, focal_box)
        else:
            rendered = _cover(image, focal_box)
        rendered.save(
            output,
            format="JPEG",
            quality=94,
            subsampling=0,
            optimize=True,
        )
    return input_size


def _cover(
    image: Image.Image,
    focal_box: tuple[float, float, float, float],
) -> Image.Image:
    target_width, target_height = 1080, 1920
    target_ratio = target_width / target_height
    width, height = image.size
    x, y, box_width, box_height = focal_box
    center_x = (x + box_width / 2) * width
    center_y = (y + box_height / 2) * height
    if width / height > target_ratio:
        crop_height = height
        crop_width = height * target_ratio
    else:
        crop_width = width
        crop_height = width / target_ratio
    left = _clamp(center_x - crop_width / 2, 0, width - crop_width)
    top = _clamp(center_y - crop_height / 2, 0, height - crop_height)
    cropped = image.crop(
        (
            round(left),
            round(top),
            round(left + crop_width),
            round(top + crop_height),
        )
    )
    return cropped.resize((target_width, target_height), Image.Resampling.LANCZOS)


def _focus_crop(
    image: Image.Image,
    focal_box: tuple[float, float, float, float],
) -> Image.Image:
    width, height = image.size
    x, y, normalized_width, normalized_height = focal_box
    center_x = (x + normalized_width / 2) * width
    center_y = (y + normalized_height / 2) * height
    region_width = min(width, normalized_width * width * 1.16)
    region_height = min(height, normalized_height * height * 1.16)
    left = _clamp(center_x - region_width / 2, 0, width - region_width)
    top = _clamp(center_y - region_height / 2, 0, height - region_height)
    focus = image.crop(
        (
            round(left),
            round(top),
            round(left + region_width),
            round(top + region_height),
        )
    )
    background = _cover(image, (0, 0, 1, 1))
    background = ImageEnhance.Brightness(background).enhance(0.42)
    focus_scale = min(972 / focus.width, 1536 / focus.height)
    focus = focus.resize(
        (
            max(1, round(focus.width * focus_scale)),
            max(1, round(focus.height * focus_scale)),
        ),
        Image.Resampling.LANCZOS,
    )

    shadow = Image.new("RGBA", (focus.width + 36, focus.height + 36), (0, 0, 0, 0))
    shadow_layer = Image.new(
        "RGBA",
        (focus.width, focus.height),
        (0, 0, 0, 165),
    )
    shadow.paste(shadow_layer, (18, 18))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=16))
    shadow_left = (1080 - shadow.width) // 2
    shadow_top = (1920 - shadow.height) // 2
    background.paste(
        (0, 0, 0),
        (shadow_left, shadow_top),
        shadow.getchannel("A"),
    )

    focus_left = (1080 - focus.width) // 2
    focus_top = (1920 - focus.height) // 2
    background.paste(focus, (focus_left, focus_top))
    return background


def _contained_background(image: Image.Image) -> Image.Image:
    background = _cover(image, (0, 0, 1, 1))
    background = ImageEnhance.Brightness(background).enhance(0.38)
    foreground = image.copy()
    foreground.thumbnail((972, 1728), Image.Resampling.LANCZOS)
    left = (1080 - foreground.width) // 2
    top = (1920 - foreground.height) // 2
    background.paste(foreground, (left, top))
    return background


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))
