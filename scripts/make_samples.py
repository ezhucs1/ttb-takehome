"""Render the bundled sample labels and their manifest.

Each sample is a label drawn from known text in one of several visual styles, optionally
passed through a "photograph" simulation (bottle curvature, perspective, glare, grain,
blur, JPEG artifacts). The manifest records the application data each label should be
checked against, the ground-truth extraction (used by the offline demo extractor), and
the recommendation the engine is expected to produce.

    .venv/bin/python scripts/make_samples.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from labelverify.engine.warning import STATUTORY_BODY

OUT = Path(__file__).resolve().parent.parent / "labelverify" / "samples"
FONT_DIRS = [Path("/usr/share/fonts/truetype"), Path("/usr/share/fonts")]

W, H = 1000, 1400


# --------------------------------------------------------------------------- fonts


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for base in FONT_DIRS:
        for path in base.rglob(name):
            return ImageFont.truetype(str(path), size)
    raise SystemExit(f"Font {name} not found; install fonts-dejavu and fonts-liberation.")


F = {
    "serif_bold": "DejaVuSerif-Bold.ttf",
    "serif": "LiberationSerif-Regular.ttf",
    "serif_italic": "LiberationSerif-Italic.ttf",
    "serif_lib_bold": "LiberationSerif-Bold.ttf",
    "sans": "LiberationSans-Regular.ttf",
    "sans_bold": "LiberationSans-Bold.ttf",
    "sans_black": "DejaVuSans-Bold.ttf",
    "mono": "LiberationMono-Regular.ttf",
}


def f(key: str, size: int) -> ImageFont.FreeTypeFont:
    return font(F[key], size)


# --------------------------------------------------------------------------- drawing helpers


def centered(draw: ImageDraw.ImageDraw, y: int, text: str, fnt, fill, spacing: int = 0) -> int:
    """Draw centered text with optional letter spacing; return the new y."""
    if spacing:
        widths = [draw.textlength(ch, font=fnt) for ch in text]
        total = sum(widths) + spacing * (len(text) - 1)
        x = (W - total) / 2
        for ch, w in zip(text, widths, strict=True):
            draw.text((x, y), ch, font=fnt, fill=fill)
            x += w + spacing
    else:
        w = draw.textlength(text, font=fnt)
        draw.text(((W - w) / 2, y), text, font=fnt, fill=fill)
    bbox = fnt.getbbox("Hg")
    return y + (bbox[3] - bbox[1]) + 10


def rule(draw, y: int, color, width: int = 2, inset: int = 120, diamond: bool = False) -> None:
    draw.line([(inset, y), (W - inset, y)], fill=color, width=width)
    if diamond:
        cx = W // 2
        draw.polygon([(cx, y - 8), (cx + 8, y), (cx, y + 8), (cx - 8, y)], fill=color)


def ornate_border(draw, color, inset: int = 34) -> None:
    draw.rectangle([inset, inset, W - inset, H - inset], outline=color, width=6)
    i2 = inset + 14
    draw.rectangle([i2, i2, W - i2, H - i2], outline=color, width=2)
    for x, y in ((i2, i2), (W - i2, i2), (i2, H - i2), (W - i2, H - i2)):
        draw.rectangle([x - 9, y - 9, x + 9, y + 9], fill=color)


def paper(color: tuple[int, int, int], seed: int, grain: float = 5.0) -> Image.Image:
    rng = np.random.default_rng(seed)
    base = np.full((H, W, 3), color, dtype=np.float32)
    noise = rng.normal(0, grain, (H, W, 1)).astype(np.float32)
    fibers = rng.normal(0, grain / 2, (H, 1, 1)).astype(np.float32)  # faint horizontal fibres
    img = np.clip(base + noise + fibers, 0, 255).astype(np.uint8)
    return Image.fromarray(img, "RGB")


def warning_block(
    draw,
    y: int,
    heading: str | None,
    body: str,
    *,
    bold_heading: bool,
    size: int,
    color,
    margin: int = 90,
) -> None:
    if heading is None:
        return
    head_font = f("sans_bold" if bold_heading else "sans", size)
    body_font = f("sans", size)
    x = margin
    max_x = W - margin
    line_h = int(size * 1.42)
    draw.text((x, y), heading + " ", font=head_font, fill=color)
    x += draw.textlength(heading + " ", font=head_font)
    for word in body.split():
        piece = word + " "
        pw = draw.textlength(piece, font=body_font)
        if x + pw > max_x:
            x = margin
            y += line_h
        draw.text((x, y), piece, font=body_font, fill=color)
        x += pw


def barcode(draw, x: int, y: int, color, seed: int = 1) -> None:
    rng = np.random.default_rng(seed)
    cx = x
    for _ in range(46):
        w = int(rng.integers(2, 6))
        if rng.random() < 0.55:
            draw.rectangle([cx, y, cx + w, y + 54], fill=color)
        cx += w + 2
    draw.text((x + 6, y + 58), "0 12345 67890 5", font=f("mono", 15), fill=color)


# --------------------------------------------------------------------------- label styles


def style_spirits(s: dict) -> Image.Image:
    ink = (43, 29, 29)
    gold = (150, 112, 44)
    img = paper(s.get("paper", (247, 241, 228)), s["seed"])
    d = ImageDraw.Draw(img)
    ornate_border(d, ink)
    y = 120
    y = centered(d, y, s.get("kicker", "SMALL BATCH"), f("sans", 22), gold, spacing=8)
    y += 16
    for line in s["brand_lines"]:
        y = centered(d, y, line, f("serif_bold", 78 if len(line) <= 10 else 64), ink)
    y += 4
    rule(d, y, gold, 3, 200, diamond=True)
    y += 26
    y = centered(d, y, s["class_line"], f("serif", 38), ink)
    if s.get("tagline"):
        y = centered(d, y + 2, s["tagline"], f("serif_italic", 27), (110, 90, 90))
    # Medallion
    cx, cy, r = W // 2, y + 120, 92
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=gold, width=4)
    d.ellipse([cx - r + 10, cy - r + 10, cx + r - 10, cy + r - 10], outline=gold, width=2)
    d.text(
        (cx, cy - 26), s.get("medal_top", "AGED"), font=f("sans_bold", 20), fill=gold, anchor="mm"
    )
    d.text((cx, cy + 6), s.get("medal_big", "6"), font=f("serif_bold", 54), fill=ink, anchor="mm")
    d.text(
        (cx, cy + 46),
        s.get("medal_bottom", "YEARS"),
        font=f("sans_bold", 18),
        fill=gold,
        anchor="mm",
    )
    y = cy + r + 40
    stats = "     ".join(t for t in (s.get("alcohol_line"), s["net_line"]) if t)
    y = centered(d, y, stats, f("sans_bold", 34), ink)
    y += 10
    rule(d, y, gold, 2, 260)
    y += 22
    for line in s["producer_lines"] + s.get("origin_lines", []):
        y = centered(d, y, line, f("sans", 22), ink)
    warning_block(
        d,
        H - 300,
        s.get("warning_heading"),
        s.get("warning_body", STATUTORY_BODY),
        bold_heading=s.get("warning_bold", True),
        size=19,
        color=ink,
    )
    barcode(d, 700, H - 150, ink, s["seed"])
    d.text(
        (92, H - 120), s.get("footer", "PRODUCT OF U.S.A. · 750 mL"), font=f("sans", 15), fill=ink
    )
    return img


def style_wine(s: dict) -> Image.Image:
    ink = (40, 36, 34)
    gold = (176, 141, 60)
    img = paper(s.get("paper", (252, 250, 244)), s["seed"], grain=3.5)
    d = ImageDraw.Draw(img)
    d.rectangle([48, 48, W - 48, H - 48], outline=gold, width=2)
    d.rectangle([58, 58, W - 58, H - 58], outline=gold, width=1)
    y = 150
    y = centered(d, y, s.get("kicker", "ESTATE GROWN"), f("sans", 20), gold, spacing=10)
    y += 30
    for line in s["brand_lines"]:
        y = centered(d, y, line, f("serif_lib_bold", 74), ink, spacing=6)
    y += 10
    rule(d, y, gold, 1, 300)
    y += 30
    y = centered(d, y, s.get("vintage", "2021"), f("serif", 60), ink)
    y = centered(d, y, s["class_line"], f("serif_italic", 40), ink)
    if s.get("tagline"):
        y = centered(d, y + 4, s["tagline"], f("serif", 26), (100, 96, 92), spacing=3)
    y += 60
    # Simple vine ornament
    cx = W // 2
    d.arc([cx - 60, y, cx, y + 40], 0, 180, fill=gold, width=2)
    d.arc([cx, y, cx + 60, y + 40], 0, 180, fill=gold, width=2)
    d.ellipse([cx - 6, y + 14, cx + 6, y + 26], fill=gold)
    y += 90
    stats = "     ".join(t for t in (s.get("alcohol_line"), s["net_line"]) if t)
    y = centered(d, y, stats, f("sans", 28), ink)
    y += 20
    for line in s["producer_lines"] + s.get("origin_lines", []):
        y = centered(d, y, line, f("sans", 20), ink)
    warning_block(
        d,
        H - 280,
        s.get("warning_heading"),
        s.get("warning_body", STATUTORY_BODY),
        bold_heading=s.get("warning_bold", True),
        size=18,
        color=ink,
        margin=110,
    )
    d.text((110, H - 110), s.get("footer", "CONTAINS SULFITES"), font=f("sans", 15), fill=ink)
    barcode(d, 690, H - 150, ink, s["seed"])
    return img


def style_beer(s: dict) -> Image.Image:
    bg = s.get("paper", (18, 78, 92))
    accent = s.get("accent", (247, 178, 44))
    ink = (255, 251, 240)
    img = paper(bg, s["seed"], grain=2.5)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 90], fill=accent)
    d.text(
        (W // 2, 45),
        s.get("kicker", "HARBOR LIGHT BREWING CO. · PORTLAND, MAINE"),
        font=f("sans_bold", 22),
        fill=bg,
        anchor="mm",
    )
    y = 200
    for line in s["brand_lines"]:
        y = centered(d, y, line, f("sans_black", 96 if len(line) <= 8 else 72), ink)
    y += 10
    y = centered(d, y, s["class_line"].upper(), f("sans_bold", 58), accent, spacing=6)
    if s.get("tagline"):
        y = centered(d, y + 10, s["tagline"], f("sans", 26), ink)
    # Wave ornament
    for i in range(3):
        yy = y + 40 + i * 22
        pts = [(x, yy + 10 * math.sin(x / 40 + i)) for x in range(120, W - 120, 8)]
        d.line(pts, fill=accent, width=3)
    y += 150
    stats = "      ".join(t for t in (s.get("alcohol_line"), s["net_line"]) if t)
    y = centered(d, y, stats, f("sans_bold", 40), ink)
    y += 30
    for line in s["producer_lines"] + s.get("origin_lines", []):
        y = centered(d, y, line, f("sans", 22), ink)
    d.rectangle([70, H - 330, W - 70, H - 110], outline=accent, width=2)
    warning_block(
        d,
        H - 310,
        s.get("warning_heading"),
        s.get("warning_body", STATUTORY_BODY),
        bold_heading=s.get("warning_bold", True),
        size=19,
        color=ink,
        margin=95,
    )
    if not s.get("warning_heading"):
        d.text(
            (95, H - 300),
            s.get("no_warning_text", "Please recycle. Brewed fresh; keep cold."),
            font=f("sans", 19),
            fill=ink,
        )
    d.text(
        (W // 2, H - 60),
        s.get("footer", "12 FL OZ (355 mL)  ·  PLEASE RECYCLE"),
        font=f("sans", 18),
        fill=ink,
        anchor="mm",
    )
    return img


def style_scotch(s: dict) -> Image.Image:
    bg = s.get("paper", (24, 32, 46))
    cream = (236, 224, 196)
    gold = (196, 160, 82)
    img = paper(bg, s["seed"], grain=3.0)
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, W - 40, H - 40], outline=gold, width=3)
    d.rectangle([52, 52, W - 52, H - 52], outline=gold, width=1)
    y = 130
    y = centered(d, y, s.get("kicker", "SPEYSIDE"), f("sans", 24), gold, spacing=12)
    y += 20
    for line in s["brand_lines"]:
        y = centered(d, y, line, f("serif_bold", 84 if len(line) <= 10 else 66), cream, spacing=4)
    y += 6
    rule(d, y, gold, 2, 220, diamond=True)
    y += 30
    y = centered(d, y, s["class_line"], f("serif", 40), cream)
    if s.get("tagline"):
        y = centered(d, y + 2, s["tagline"], f("serif_italic", 28), gold)
    # Thistle-ish crest: three lozenges
    cx, cy = W // 2, y + 110
    for dx in (-70, 0, 70):
        d.polygon(
            [(cx + dx, cy - 40), (cx + dx + 26, cy), (cx + dx, cy + 40), (cx + dx - 26, cy)],
            outline=gold,
            width=3,
        )
    d.text((cx, cy), s.get("medal_big", "12"), font=f("serif_bold", 36), fill=cream, anchor="mm")
    y = cy + 80
    stats = "     ".join(t for t in (s.get("alcohol_line"), s["net_line"]) if t)
    y = centered(d, y, stats, f("sans_bold", 34), cream)
    y += 10
    rule(d, y, gold, 1, 300)
    y += 22
    for line in s["producer_lines"] + s.get("origin_lines", []):
        y = centered(d, y, line, f("sans", 22), cream)
    warning_block(
        d,
        H - 300,
        s.get("warning_heading"),
        s.get("warning_body", STATUTORY_BODY),
        bold_heading=s.get("warning_bold", True),
        size=19,
        color=cream,
    )
    barcode(d, 700, H - 150, cream, s["seed"])
    d.text(
        (92, H - 120),
        s.get("footer", "DISTILLED, MATURED AND BOTTLED IN SCOTLAND"),
        font=f("sans", 15),
        fill=cream,
    )
    return img


STYLES = {"spirits": style_spirits, "wine": style_wine, "beer": style_beer, "scotch": style_scotch}


# --------------------------------------------------------------------------- photo simulation


def _perspective_coeffs(src, dst):
    matrix = []
    for (x, y), (u, v) in zip(src, dst, strict=True):
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    a = np.array(matrix, dtype=np.float64)
    b = np.array([c for pt in dst for c in pt], dtype=np.float64)
    return np.linalg.solve(a, b)


def photograph(
    label: Image.Image,
    *,
    seed: int,
    curvature: float = 0.55,
    tilt: float = 0.10,
    rotate: float = 3.0,
    glare: float = 0.45,
    blur: float = 0.8,
    grain: float = 7.0,
) -> Image.Image:
    """Turn flat artwork into something like a phone photo of a bottle on a shelf."""
    rng = np.random.default_rng(seed)
    # Bottle curvature: darken toward the left/right edges.
    arr = np.asarray(label).astype(np.float32)
    xs = np.linspace(-1, 1, arr.shape[1])
    shade = 1 - curvature * (np.abs(xs) ** 2.2)
    arr *= shade[None, :, None]
    label = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))

    # Scene: dark wooden shelf gradient behind the bottle.
    cw, ch = 1300, 1750
    scene = np.zeros((ch, cw, 3), dtype=np.float32)
    vertical = np.linspace(0, 1, ch)[:, None]
    scene[..., 0] = 60 + 40 * vertical
    scene[..., 1] = 48 + 28 * vertical
    scene[..., 2] = 40 + 18 * vertical
    scene += rng.normal(0, 3, scene.shape)
    canvas = Image.fromarray(np.clip(scene, 0, 255).astype(np.uint8))
    # Bottle body: slightly lighter vertical band.
    bd = ImageDraw.Draw(canvas)
    bd.rounded_rectangle([110, 60, cw - 110, ch - 40], radius=80, fill=(52, 44, 40))
    canvas.paste(
        label.resize((int(W * 0.94), int(H * 0.94))),
        ((cw - int(W * 0.94)) // 2, (ch - int(H * 0.94)) // 2),
    )

    # Perspective: camera slightly to one side and below.
    src = [(0, 0), (cw, 0), (cw, ch), (0, ch)]
    dx = int(cw * tilt)
    dst = [(dx, int(ch * 0.03)), (cw - dx // 3, 0), (cw, ch), (0, ch - int(ch * 0.04))]
    coeffs = _perspective_coeffs(dst, src)
    canvas = canvas.transform(
        (cw, ch), Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BICUBIC
    )
    canvas = canvas.rotate(
        rotate, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=(58, 46, 40)
    )

    # Glare: a soft diagonal highlight, like a window reflection on glass.
    if glare:
        layer = Image.new("L", (cw, ch), 0)
        ld = ImageDraw.Draw(layer)
        ld.ellipse([cw * 0.15, ch * 0.05, cw * 0.55, ch * 0.75], fill=int(255 * glare))
        layer = layer.rotate(-25, resample=Image.Resampling.BICUBIC).filter(
            ImageFilter.GaussianBlur(120)
        )
        white = Image.new("RGB", (cw, ch), (255, 255, 250))
        canvas = Image.composite(white, canvas, layer)

    if blur:
        canvas = canvas.filter(ImageFilter.GaussianBlur(blur))

    arr = np.asarray(canvas).astype(np.float32)
    if grain:
        arr += rng.normal(0, grain, arr.shape)
    # Vignette.
    yy, xx = np.mgrid[0:ch, 0:cw]
    r = np.sqrt(((xx - cw / 2) / (cw / 2)) ** 2 + ((yy - ch / 2) / (ch / 2)) ** 2)
    arr *= (1 - 0.35 * np.clip(r - 0.55, 0, 1))[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


# --------------------------------------------------------------------------- sample definitions


def field(value: str | None, confidence: float = 0.97) -> dict:
    return {"value": value, "confidence": confidence if value else 0.0}


def warning(
    heading: str | None,
    *,
    bold: bool | None = True,
    body: str = STATUTORY_BODY,
    confidence: float = 0.96,
) -> dict:
    if heading is None:
        return {
            "present": False,
            "text": None,
            "heading_all_caps": None,
            "heading_bold": None,
            "confidence": 0.0,
        }
    caps = heading.rstrip(":") == heading.rstrip(":").upper()
    return {
        "present": True,
        "text": f"{heading} {body}",
        "heading_all_caps": caps,
        "heading_bold": bold,
        "confidence": confidence,
    }


def clear() -> dict:
    return {"readable": True, "issues": []}


OLD_TOM_APP = {
    "beverage_type": "distilled_spirits",
    "brand_name": "OLD TOM DISTILLERY",
    "class_type": "Kentucky Straight Bourbon Whiskey",
    "alcohol_content": "45% Alc./Vol. (90 Proof)",
    "net_contents": "750 mL",
    "producer_name": "Old Tom Distillery",
    "producer_address": "Bardstown, KY 40004",
    "is_import": False,
    "country_of_origin": "",
}

OLD_TOM_LABEL = {
    "style": "spirits",
    "kicker": "SMALL BATCH · SOUR MASH",
    "brand_lines": ["OLD TOM", "DISTILLERY"],
    "class_line": "Kentucky Straight Bourbon Whiskey",
    "tagline": "Aged Six Years in New Charred Oak Barrels",
    "alcohol_line": "45% Alc./Vol. (90 Proof)",
    "net_line": "750 mL",
    "producer_lines": ["Distilled and Bottled by Old Tom Distillery", "Bardstown, Kentucky 40004"],
}


def old_tom_extraction(**overrides) -> dict:
    base = {
        "brand_name": field("OLD TOM DISTILLERY"),
        "class_type": field("Kentucky Straight Bourbon Whiskey"),
        "alcohol_content": field("45% Alc./Vol. (90 Proof)"),
        "net_contents": field("750 mL"),
        "producer_name": field("Old Tom Distillery"),
        "producer_address": field("Bardstown, Kentucky 40004"),
        "country_of_origin": field(None),
        "health_warning": warning("GOVERNMENT WARNING:"),
        "image_quality": clear(),
    }
    base.update(overrides)
    return base


SAMPLES: list[dict] = [
    {
        "id": "old-tom-bourbon",
        "title": "Old Tom Bourbon · clean artwork",
        "description": "The sample from the brief as print-ready artwork. Everything matches.",
        "expected": "approve",
        "seed": 1,
        "label": {**OLD_TOM_LABEL, "warning_heading": "GOVERNMENT WARNING:"},
        "application": OLD_TOM_APP,
        "extraction": old_tom_extraction(),
    },
    {
        "id": "old-tom-title-case-warning",
        "title": "Old Tom Bourbon · title-case warning",
        "description": "Same artwork, but the heading reads 'Government Warning:'. Jenny's rejection case.",
        "expected": "request_correction",
        "seed": 2,
        "label": {**OLD_TOM_LABEL, "warning_heading": "Government Warning:"},
        "application": OLD_TOM_APP,
        "extraction": old_tom_extraction(health_warning=warning("Government Warning:")),
    },
    {
        "id": "old-tom-abv-mismatch",
        "title": "Old Tom Bourbon · bottle photo, ABV mismatch",
        "description": "Photographed on the bottle. Label says 40% / 80 proof; the application says 45%.",
        "expected": "request_correction",
        "seed": 3,
        "photo": {"tilt": 0.05, "rotate": -1.5, "glare": 0.3, "blur": 0.6},
        "label": {
            **OLD_TOM_LABEL,
            "alcohol_line": "40% Alc./Vol. (80 Proof)",
            "warning_heading": "GOVERNMENT WARNING:",
        },
        "application": OLD_TOM_APP,
        "extraction": old_tom_extraction(
            alcohol_content=field("40% Alc./Vol. (80 Proof)", 0.93),
            producer_address=field("Bardstown, Kentucky 40004", 0.88),
            health_warning=warning("GOVERNMENT WARNING:", confidence=0.9),
            image_quality={
                "readable": True,
                "issues": ["Mild reflection on the glass near the top left."],
            },
        ),
    },
    {
        "id": "old-tom-angled-photo",
        "title": "Old Tom Bourbon · angled phone photo",
        "description": "Steep angle, glare, and grain. Readable, but the small print comes back with lower confidence.",
        "expected": "needs_review",
        "seed": 4,
        "photo": {"tilt": 0.16, "rotate": 5.0, "glare": 0.55, "blur": 1.1, "grain": 10.0},
        "label": {**OLD_TOM_LABEL, "warning_heading": "GOVERNMENT WARNING:"},
        "application": OLD_TOM_APP,
        "extraction": old_tom_extraction(
            brand_name=field("OLD TOM DISTILLERY", 0.9),
            class_type=field("Kentucky Straight Bourbon Whiskey", 0.84),
            alcohol_content=field("45% Alc./Vol. (90 Proof)", 0.8),
            net_contents=field("750 mL", 0.86),
            producer_name=field("Old Tom Distillery", 0.72),
            producer_address=field("Bardstown, Kentucky 40004", 0.55),
            health_warning=warning("GOVERNMENT WARNING:", bold=None, confidence=0.7),
            image_quality={
                "readable": True,
                "issues": [
                    "Photographed at a steep angle with glare across the upper half; small print is soft."
                ],
            },
        ),
    },
    {
        "id": "stones-throw-wine",
        "title": "Stone's Throw Cabernet · case difference",
        "description": "Label prints STONE'S THROW in capitals; the application says Stone's Throw. Dave's example.",
        "expected": "approve",
        "seed": 5,
        "label": {
            "style": "wine",
            "kicker": "NAPA VALLEY",
            "brand_lines": ["STONE'S THROW"],
            "vintage": "2021",
            "class_line": "Cabernet Sauvignon",
            "tagline": "ESTATE BOTTLED · ST. HELENA",
            "alcohol_line": "14.5% Alc. by Vol.",
            "net_line": "750 mL",
            "producer_lines": [
                "Produced and Bottled by Stone's Throw Vineyards",
                "St. Helena, California",
            ],
            "warning_heading": "GOVERNMENT WARNING:",
        },
        "application": {
            "beverage_type": "wine",
            "brand_name": "Stone's Throw",
            "class_type": "Cabernet Sauvignon",
            "alcohol_content": "14.5",
            "net_contents": "750 mL",
            "producer_name": "Stone's Throw Vineyards",
            "producer_address": "St. Helena, CA",
            "is_import": False,
            "country_of_origin": "",
        },
        "extraction": {
            "brand_name": field("STONE'S THROW"),
            "class_type": field("Cabernet Sauvignon"),
            "alcohol_content": field("14.5% Alc. by Vol."),
            "net_contents": field("750 mL"),
            "producer_name": field("Stone's Throw Vineyards"),
            "producer_address": field("St. Helena, California"),
            "country_of_origin": field(None),
            "health_warning": warning("GOVERNMENT WARNING:"),
            "image_quality": clear(),
        },
    },
    {
        "id": "sunset-rose-altered-warning",
        "title": "Sunset Ridge Rosé · reworded warning",
        "description": "The warning ends 'can cause health issues' instead of 'may cause health problems'.",
        "expected": "request_correction",
        "seed": 6,
        "label": {
            "style": "wine",
            "paper": (253, 244, 240),
            "kicker": "SONOMA COAST",
            "brand_lines": ["SUNSET RIDGE"],
            "vintage": "2023",
            "class_line": "Rosé Wine",
            "tagline": "DRY · PINOT NOIR GRAPES",
            "alcohol_line": "12.5% Alc. by Vol.",
            "net_line": "750 mL",
            "producer_lines": [
                "Vinted and Bottled by Sunset Ridge Cellars",
                "Sebastopol, California",
            ],
            "warning_heading": "GOVERNMENT WARNING:",
            "warning_body": STATUTORY_BODY.replace(
                "and may cause health problems.", "and can cause health issues."
            ),
        },
        "application": {
            "beverage_type": "wine",
            "brand_name": "Sunset Ridge",
            "class_type": "Rosé Wine",
            "alcohol_content": "12.5%",
            "net_contents": "750 mL",
            "producer_name": "Sunset Ridge Cellars",
            "producer_address": "Sebastopol, CA",
            "is_import": False,
            "country_of_origin": "",
        },
        "extraction": {
            "brand_name": field("SUNSET RIDGE"),
            "class_type": field("Rosé Wine"),
            "alcohol_content": field("12.5% Alc. by Vol."),
            "net_contents": field("750 mL"),
            "producer_name": field("Sunset Ridge Cellars"),
            "producer_address": field("Sebastopol, California"),
            "country_of_origin": field(None),
            "health_warning": warning(
                "GOVERNMENT WARNING:",
                body=STATUTORY_BODY.replace(
                    "and may cause health problems.", "and can cause health issues."
                ),
            ),
            "image_quality": clear(),
        },
    },
    {
        "id": "glen-aldie-scotch",
        "title": "Glen Aldie Scotch · import",
        "description": "Imported single malt with 'Product of Scotland'; the application lists United Kingdom.",
        "expected": "approve",
        "seed": 7,
        "label": {
            "style": "scotch",
            "brand_lines": ["GLEN ALDIE"],
            "class_line": "Single Malt Scotch Whisky",
            "tagline": "Matured in Oloroso Sherry Casks",
            "medal_big": "12",
            "alcohol_line": "43% Alc./Vol. (86 Proof)",
            "net_line": "750 mL",
            "producer_lines": [
                "Distilled and Bottled by Glen Aldie Distillers, Speyside, Scotland",
                "Imported by Caledonia Imports, Newark, New Jersey",
            ],
            "origin_lines": ["PRODUCT OF SCOTLAND"],
            "warning_heading": "GOVERNMENT WARNING:",
        },
        "application": {
            "beverage_type": "distilled_spirits",
            "brand_name": "Glen Aldie",
            "class_type": "Single Malt Scotch Whiskey",
            "alcohol_content": "43%",
            "net_contents": "750 mL",
            "producer_name": "Glen Aldie Distillers",
            "producer_address": "Speyside, Scotland",
            "is_import": True,
            "country_of_origin": "United Kingdom",
        },
        "extraction": {
            "brand_name": field("GLEN ALDIE"),
            "class_type": field("Single Malt Scotch Whisky"),
            "alcohol_content": field("43% Alc./Vol. (86 Proof)"),
            "net_contents": field("750 mL"),
            "producer_name": field("Glen Aldie Distillers"),
            "producer_address": field("Speyside, Scotland"),
            "country_of_origin": field("PRODUCT OF SCOTLAND"),
            "health_warning": warning("GOVERNMENT WARNING:"),
            "image_quality": clear(),
        },
    },
    {
        "id": "harbor-light-ipa-missing-warning",
        "title": "Harbor Light IPA · no warning",
        "description": "Can artwork with no Government Health Warning Statement at all.",
        "expected": "request_correction",
        "seed": 8,
        "label": {
            "style": "beer",
            "brand_lines": ["HARBOR", "LIGHT"],
            "class_line": "India Pale Ale",
            "tagline": "Brewed with Citra and Mosaic hops",
            "alcohol_line": "6.8% ALC/VOL",
            "net_line": "12 FL OZ (355 mL)",
            "producer_lines": [
                "Brewed and Canned by Harbor Light Brewing Co.",
                "Portland, Maine 04101",
            ],
            "warning_heading": None,
        },
        "application": {
            "beverage_type": "malt_beverage",
            "brand_name": "Harbor Light",
            "class_type": "IPA",
            "alcohol_content": "6.8",
            "net_contents": "12 fl oz",
            "producer_name": "Harbor Light Brewing Co.",
            "producer_address": "Portland, ME 04101",
            "is_import": False,
            "country_of_origin": "",
        },
        "extraction": {
            "brand_name": field("HARBOR LIGHT"),
            "class_type": field("India Pale Ale"),
            "alcohol_content": field("6.8% ALC/VOL"),
            "net_contents": field("12 FL OZ (355 mL)"),
            "producer_name": field("Harbor Light Brewing Co."),
            "producer_address": field("Portland, Maine 04101"),
            "country_of_origin": field(None),
            "health_warning": warning(None),
            "image_quality": clear(),
        },
    },
    {
        "id": "harbor-light-ipa-net-contents",
        "title": "Harbor Light IPA · net contents mismatch",
        "description": "Pint can (16 fl oz) submitted against an application that says 12 fl oz.",
        "expected": "request_correction",
        "seed": 9,
        "photo": {"tilt": 0.08, "rotate": 2.0, "glare": 0.35, "blur": 0.7, "curvature": 0.7},
        "label": {
            "style": "beer",
            "paper": (170, 52, 40),
            "accent": (250, 226, 160),
            "brand_lines": ["HARBOR", "LIGHT"],
            "class_line": "India Pale Ale",
            "tagline": "Brewed with Citra and Mosaic hops",
            "alcohol_line": "6.8% ALC/VOL",
            "net_line": "16 FL OZ (473 mL)",
            "producer_lines": [
                "Brewed and Canned by Harbor Light Brewing Co.",
                "Portland, Maine 04101",
            ],
            "warning_heading": "GOVERNMENT WARNING:",
            "footer": "16 FL OZ (473 mL)  ·  PLEASE RECYCLE",
        },
        "application": {
            "beverage_type": "malt_beverage",
            "brand_name": "Harbor Light",
            "class_type": "IPA",
            "alcohol_content": "6.8",
            "net_contents": "12 fl oz",
            "producer_name": "Harbor Light Brewing Co.",
            "producer_address": "Portland, ME 04101",
            "is_import": False,
            "country_of_origin": "",
        },
        "extraction": {
            "brand_name": field("HARBOR LIGHT"),
            "class_type": field("India Pale Ale"),
            "alcohol_content": field("6.8% ALC/VOL", 0.94),
            "net_contents": field("16 FL OZ (473 mL)", 0.95),
            "producer_name": field("Harbor Light Brewing Co.", 0.9),
            "producer_address": field("Portland, Maine 04101", 0.86),
            "country_of_origin": field(None),
            "health_warning": warning("GOVERNMENT WARNING:", confidence=0.9),
            "image_quality": {
                "readable": True,
                "issues": ["Curved can surface; text near the edges is compressed."],
            },
        },
    },
    {
        "id": "copper-ridge-rye-typo",
        "title": "Copper Ridge Rye · brand typo",
        "description": "The label prints COPPER RIGDE (transposed letters). Similar, not identical, so a specialist decides.",
        "expected": "needs_review",
        "seed": 10,
        "label": {
            **OLD_TOM_LABEL,
            "paper": (236, 226, 204),
            "kicker": "BOTTLED IN BOND",
            "brand_lines": ["COPPER", "RIGDE"],
            "class_line": "Straight Rye Whiskey",
            "tagline": "95% Rye Mash Bill · Non-Chill Filtered",
            "medal_top": "AGED",
            "medal_big": "4",
            "medal_bottom": "YEARS",
            "alcohol_line": "50% Alc./Vol. (100 Proof)",
            "net_line": "750 mL",
            "producer_lines": [
                "Distilled and Bottled by Copper Ridge Distilling Co.",
                "Lawrenceburg, Indiana 47025",
            ],
            "warning_heading": "GOVERNMENT WARNING:",
        },
        "application": {
            "beverage_type": "distilled_spirits",
            "brand_name": "Copper Ridge",
            "class_type": "Straight Rye Whiskey",
            "alcohol_content": "50% (100 proof)",
            "net_contents": "750 mL",
            "producer_name": "Copper Ridge Distilling Co.",
            "producer_address": "Lawrenceburg, IN 47025",
            "is_import": False,
            "country_of_origin": "",
        },
        "extraction": {
            "brand_name": field("COPPER RIGDE"),
            "class_type": field("Straight Rye Whiskey"),
            "alcohol_content": field("50% Alc./Vol. (100 Proof)"),
            "net_contents": field("750 mL"),
            "producer_name": field("Copper Ridge Distilling Co."),
            "producer_address": field("Lawrenceburg, Indiana 47025"),
            "country_of_origin": field(None),
            "health_warning": warning("GOVERNMENT WARNING:"),
            "image_quality": clear(),
        },
    },
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    for old in OUT.glob("*.jpg"):
        old.unlink()
    manifest = []
    for sample in SAMPLES:
        spec = {**sample["label"], "seed": sample["seed"]}
        image = STYLES[spec["style"]](spec)
        filename = f"{sample['id']}.jpg"
        if sample.get("photo") is not None:
            image = photograph(image, seed=sample["seed"], **sample["photo"])
            image.save(OUT / filename, format="JPEG", quality=82, optimize=True)
        else:  # artwork: high quality, still far smaller than PNG with paper grain
            image.save(OUT / filename, format="JPEG", quality=90, optimize=True, subsampling=0)
        manifest.append(
            {
                "id": sample["id"],
                "title": sample["title"],
                "description": sample["description"],
                "file": filename,
                "expected": sample["expected"],
                "application": sample["application"],
                "extraction": sample["extraction"],
            }
        )
        print(f"wrote {filename}")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote manifest.json ({len(manifest)} samples)")


if __name__ == "__main__":
    main()
