import os
from PIL import Image, ImageDraw, ImageFont


def _find_font(size: int):
    """
    Try a few common bold font paths (Windows, then Linux/Mac) before
    falling back to Pillow's built-in default font.
    """
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue

    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def generate_watermark_png(text: str, output_path: str, font_size: int = 42, opacity: int = 160) -> str:
    """
    Renders `text` as a semi-transparent watermark PNG with padding,
    ready to be overlaid onto a video corner via ffmpeg's overlay filter.
    opacity: 0 (invisible) to 255 (fully opaque).
    """
    clean_text = (text or "").strip()
    if not clean_text:
        clean_text = "Clip Bay"

    font = _find_font(font_size)

    # Measure text to size the canvas exactly, then add padding
    dummy_img = Image.new("RGBA", (10, 10))
    dummy_draw = ImageDraw.Draw(dummy_img)
    bbox = dummy_draw.textbbox((0, 0), clean_text, font=font)
    text_w = max(1, bbox[2] - bbox[0])
    text_h = max(1, bbox[3] - bbox[1])

    padding_x, padding_y = 20, 12
    canvas_w = text_w + padding_x * 2
    canvas_h = text_h + padding_y * 2

    img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Subtle dark backing pill behind the text so it stays readable on any
    # background, plus a soft drop shadow on the text itself.
    draw.rounded_rectangle(
        [0, 0, canvas_w, canvas_h], radius=canvas_h // 3, fill=(0, 0, 0, int(opacity * 0.5))
    )
    shadow_offset = 2
    draw.text(
        (padding_x - bbox[0] + shadow_offset, padding_y - bbox[1] + shadow_offset),
        clean_text, font=font, fill=(0, 0, 0, int(opacity * 0.8)),
    )
    draw.text(
        (padding_x - bbox[0], padding_y - bbox[1]),
        clean_text, font=font, fill=(255, 255, 255, opacity),
    )

    parent_dir = os.path.dirname(output_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    img.save(output_path)
    return output_path
