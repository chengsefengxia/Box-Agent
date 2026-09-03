"""Text watermark rendering for generated bitmap images.

This module overlays a small text watermark (default ``AI 生成``) onto a
generated image before it is written to disk. It is shared by both the
text-to-image and image-to-image paths of :class:`GenerateImageTool`, so a
single call site covers CLI and ACP runtimes alike.

Design constraints:
- Pillow is an optional (``runtime`` extra) dependency, so it is imported
  lazily and any failure degrades gracefully — the original bytes are returned
  unchanged and the caller is told the watermark was skipped. Generating an
  image must never fail because watermarking failed.
- All visual parameters are hard-coded module constants; there is no config.
- Vector (SVG) and animated (GIF) formats are skipped.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from PIL import ImageFont


_DEFAULT_TEXT = "AI 生成"
_OPACITY = 0.45  # watermark fill alpha, 0.0–1.0
_COLOR = "#FFFFFF"  # watermark text color (hex)
_FONT_SIZE_RATIO = 0.022  # font size = short_edge * ratio
_MIN_FONT_SIZE = 12
_MARGIN_RATIO = 0.02  # margin from the corner = short_edge * ratio
_STROKE = True  # draw a contrasting outline so the text stays readable

# Only true bitmaps are rasterizable by Pillow. SVG is vector, GIF is animated.
_RASTERIZABLE_MIME = {"image/png", "image/jpeg", "image/jpg", "image/webp"}

# Bundled CJK font so non-Latin (e.g. Chinese) custom watermark text renders
# consistently across macOS/Windows/Linux and inside the frozen runtime. The
# The font is a runtime resource, not owned by any marketplace Skill.
_BUNDLED_FONT = (
    Path(__file__).resolve().parent.parent
    / "resources"
    / "fonts"
    / "NotoSansSC-Regular.otf"
)

# Platform system-font fallbacks (only consulted if the bundled font is missing).
_SYSTEM_FONT_CANDIDATES = (
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    # Windows
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/arial.ttf",
    # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def apply_text_watermark(
    image_bytes: bytes,
    mime_type: str,
    text: str | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Overlay a text watermark on the bottom-right of a bitmap image.

    Args:
        image_bytes: Raw encoded image bytes.
        mime_type: MIME type of ``image_bytes`` (e.g. ``image/png``).
        text: Watermark text; ``None``/empty falls back to ``AI 生成``.

    Returns:
        ``(out_bytes, status)`` where ``status`` is
        ``{"applied": bool, "reason": str | None, "font": str}``. On any
        problem (unsupported MIME, Pillow missing, decode/encode error) the
        original ``image_bytes`` are returned with ``applied=False`` — this
        function never raises.
    """
    watermark_text = (text or "").strip() or _DEFAULT_TEXT

    if mime_type not in _RASTERIZABLE_MIME:
        return image_bytes, {
            "applied": False,
            "reason": f"unsupported mime type for watermark: {mime_type}",
            "font": "",
        }

    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return image_bytes, {
            "applied": False,
            "reason": "Pillow is not installed; watermark skipped",
            "font": "",
        }

    try:
        base = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        short_edge = min(base.size)
        font_size = max(_MIN_FONT_SIZE, int(short_edge * _FONT_SIZE_RATIO))
        font, font_name = _resolve_font(font_size)
        margin = max(1, int(short_edge * _MARGIN_RATIO))

        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        fill = _hex_to_rgba(_COLOR, _OPACITY)

        text_w, text_h = _text_size(draw, watermark_text, font)
        xy = _anchor_xy(base.size, (text_w, text_h), margin)

        stroke_kwargs: dict[str, Any] = {}
        if _STROKE:
            stroke_kwargs = {
                "stroke_width": max(1, font_size // 12),
                "stroke_fill": _contrast_stroke(fill),
            }

        draw.text(xy, watermark_text, font=font, fill=fill, **stroke_kwargs)
        composited = Image.alpha_composite(base, overlay)

        out_bytes = _encode_target(composited, mime_type, Image)
        return out_bytes, {"applied": True, "reason": None, "font": font_name}
    except Exception as exc:  # pragma: no cover - defensive: never fail a render
        return image_bytes, {
            "applied": False,
            "reason": f"watermark error: {exc}",
            "font": "",
        }


def _resolve_font(size: int) -> tuple["ImageFont.FreeTypeFont | ImageFont.ImageFont", str]:
    """Resolve a font with a three-level fallback chain.

    1. Bundled CJK font (primary; guarantees consistent cross-platform + frozen).
    2. System font probing (best-effort enhancement).
    3. ``ImageFont.load_default(size=...)`` (Pillow 10+); never fails.

    Returns ``(font, font_name)`` where ``font_name`` identifies the source for
    diagnostics ("load_default" means non-Latin glyphs may render as tofu).
    """
    from PIL import ImageFont

    if _BUNDLED_FONT.exists():
        try:
            return ImageFont.truetype(str(_BUNDLED_FONT), size), _BUNDLED_FONT.name
        except OSError:
            pass

    for candidate in _SYSTEM_FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size), Path(candidate).name
            except OSError:
                continue

    try:
        return ImageFont.load_default(size=size), "load_default"
    except TypeError:  # pragma: no cover - very old Pillow without size kwarg
        return ImageFont.load_default(), "load_default"


def _text_size(draw: Any, text: str, font: Any) -> tuple[int, int]:
    """Measure rendered text using ``textbbox`` (Pillow 10+ compatible)."""
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _anchor_xy(
    img_size: tuple[int, int],
    text_size: tuple[int, int],
    margin: int,
) -> tuple[int, int]:
    """Bottom-right anchor coordinates for the text box."""
    img_w, img_h = img_size
    text_w, text_h = text_size
    # textbbox of multi-line/descender text can exceed the nominal box; pad a
    # little extra on the bottom so descenders are not clipped at the edge.
    x = max(margin, img_w - text_w - margin)
    y = max(margin, img_h - text_h - margin - int(text_h * 0.3))
    return x, y


def _hex_to_rgba(color: str, opacity: float) -> tuple[int, int, int, int]:
    """Parse a ``#RRGGBB`` color into RGBA; fall back to white on error."""
    alpha = int(max(0.0, min(1.0, opacity)) * 255)
    value = (color or "").strip().lstrip("#")
    try:
        if len(value) == 3:
            value = "".join(ch * 2 for ch in value)
        r = int(value[0:2], 16)
        g = int(value[2:4], 16)
        b = int(value[4:6], 16)
    except (ValueError, IndexError):
        r, g, b = 255, 255, 255
    return r, g, b, alpha


def _contrast_stroke(fill: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Return a stroke color that contrasts the fill (dark text→light stroke)."""
    r, g, b, a = fill
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    stroke_rgb = (0, 0, 0) if luminance > 140 else (255, 255, 255)
    return (*stroke_rgb, a)


def _encode_target(image: Any, mime_type: str, image_module: Any) -> bytes:
    """Re-encode the composited RGBA image to the original ``mime_type``.

    JPEG has no alpha channel, so the image is flattened onto a white
    background first. PNG/WebP keep transparency.
    """
    buffer = io.BytesIO()
    normalized = mime_type.lower()

    if normalized in ("image/jpeg", "image/jpg"):
        background = image_module.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[-1])  # use alpha as mask
        background.save(buffer, format="JPEG", quality=95, subsampling=0)
    elif normalized == "image/webp":
        image.save(buffer, format="WEBP", quality=95)
    else:  # image/png
        image.save(buffer, format="PNG")

    return buffer.getvalue()
