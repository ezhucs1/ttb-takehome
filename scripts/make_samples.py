"""Render the bundled sample labels and their manifest.

Each sample is a label image drawn from known text, the application data it should be
checked against, the ground-truth extraction (used by the offline demo extractor), and
the recommendation the engine is expected to produce. Re-run after changing a sample:

    .venv/bin/python scripts/make_samples.py
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from labelverify.engine.warning import STATUTORY_BODY

OUT = Path(__file__).resolve().parent.parent / "labelverify" / "samples"
FONT_DIRS = [Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/dejavu")]


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for directory in FONT_DIRS:
        path = directory / name
        if path.exists():
            return ImageFont.truetype(str(path), size)
    raise SystemExit(f"Font {name} not found; install fonts-dejavu.")


SERIF_BOLD = lambda s: font("DejaVuSerif-Bold.ttf", s)  # noqa: E731
SANS = lambda s: font("DejaVuSans.ttf", s)  # noqa: E731
SANS_BOLD = lambda s: font("DejaVuSans-Bold.ttf", s)  # noqa: E731


def render(sample: dict) -> Image.Image:
    width, height = 1000, 1400
    image = Image.new("RGB", (width, height), sample.get("paper", "#f7f2e7"))
    draw = ImageDraw.Draw(image)
    draw.rectangle([30, 30, width - 30, height - 30], outline="#3b2f2f", width=6)
    draw.rectangle([46, 46, width - 46, height - 46], outline="#3b2f2f", width=2)

    y = 140
    for line in sample["brand_lines"]:
        f = SERIF_BOLD(72 if len(line) < 16 else 56)
        w = draw.textlength(line, font=f)
        draw.text(((width - w) / 2, y), line, font=f, fill="#2b1d1d")
        y += 90

    y += 20
    f = SANS(36)
    w = draw.textlength(sample["class_line"], font=f)
    draw.text(((width - w) / 2, y), sample["class_line"], font=f, fill="#2b1d1d")
    y += 80

    if sample.get("tagline"):
        f = SANS(26)
        w = draw.textlength(sample["tagline"], font=f)
        draw.text(((width - w) / 2, y), sample["tagline"], font=f, fill="#6b5b5b")
        y += 70

    stats = "     ".join(s for s in (sample.get("alcohol_line"), sample["net_line"]) if s)
    f = SANS_BOLD(34)
    w = draw.textlength(stats, font=f)
    draw.text(((width - w) / 2, y + 40), stats, font=f, fill="#2b1d1d")
    y += 140

    f = SANS(24)
    for line in sample["producer_lines"] + sample.get("origin_lines", []):
        w = draw.textlength(line, font=f)
        draw.text(((width - w) / 2, y), line, font=f, fill="#2b1d1d")
        y += 36

    if sample.get("warning_heading"):
        y = height - 330
        heading_font = SANS_BOLD(22) if sample.get("warning_bold", True) else SANS(22)
        body_font = SANS(22)
        heading = sample["warning_heading"] + " "
        body = sample.get("warning_body", STATUTORY_BODY)
        margin = 90
        max_width = width - 2 * margin
        words = body.split()
        x = margin
        draw.text((x, y), heading, font=heading_font, fill="#1a1a1a")
        x += draw.textlength(heading, font=heading_font)
        line_height = 32
        for word in words:
            piece = word + " "
            piece_width = draw.textlength(piece, font=body_font)
            if x + piece_width > margin + max_width:
                x = margin
                y += line_height
            draw.text((x, y), piece, font=body_font, fill="#1a1a1a")
            x += piece_width

    if sample.get("rotate"):
        image = image.rotate(sample["rotate"], expand=True, fillcolor="#d9d4c7")
    return image


def field(value: str | None, confidence: float = 0.96) -> dict:
    return {"value": value, "confidence": confidence if value else 0.0}


def warning(heading: str | None, *, bold: bool = True, body: str = STATUTORY_BODY) -> dict:
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
        "confidence": 0.95,
    }


SAMPLES: list[dict] = [
    {
        "id": "old-tom-bourbon",
        "title": "Old Tom Bourbon (clean)",
        "description": "The sample from the brief. Everything matches; expected result is approve.",
        "expected": "approve",
        "brand_lines": ["OLD TOM", "DISTILLERY"],
        "class_line": "Kentucky Straight Bourbon Whiskey",
        "tagline": "Aged Six Years in New Charred Oak",
        "alcohol_line": "45% Alc./Vol. (90 Proof)",
        "net_line": "750 mL",
        "producer_lines": [
            "Distilled and Bottled by Old Tom Distillery",
            "Bardstown, Kentucky 40004",
        ],
        "warning_heading": "GOVERNMENT WARNING:",
        "application": {
            "beverage_type": "distilled_spirits",
            "brand_name": "OLD TOM DISTILLERY",
            "class_type": "Kentucky Straight Bourbon Whiskey",
            "alcohol_content": "45% Alc./Vol. (90 Proof)",
            "net_contents": "750 mL",
            "producer_name": "Old Tom Distillery",
            "producer_address": "Bardstown, KY 40004",
            "is_import": False,
            "country_of_origin": "",
        },
        "extraction": {
            "brand_name": field("OLD TOM DISTILLERY"),
            "class_type": field("Kentucky Straight Bourbon Whiskey"),
            "alcohol_content": field("45% Alc./Vol. (90 Proof)"),
            "net_contents": field("750 mL"),
            "producer_name": field("Old Tom Distillery"),
            "producer_address": field("Bardstown, Kentucky 40004"),
            "country_of_origin": field(None),
            "health_warning": warning("GOVERNMENT WARNING:"),
            "image_quality": {"readable": True, "issues": []},
        },
    },
    {
        "id": "old-tom-title-case-warning",
        "title": "Old Tom Bourbon (title-case warning)",
        "description": "Same label, but the warning heading reads 'Government Warning:'. Expected: request correction.",
        "expected": "request_correction",
        "brand_lines": ["OLD TOM", "DISTILLERY"],
        "class_line": "Kentucky Straight Bourbon Whiskey",
        "tagline": "Aged Six Years in New Charred Oak",
        "alcohol_line": "45% Alc./Vol. (90 Proof)",
        "net_line": "750 mL",
        "producer_lines": [
            "Distilled and Bottled by Old Tom Distillery",
            "Bardstown, Kentucky 40004",
        ],
        "warning_heading": "Government Warning:",
        "application": None,  # same as old-tom-bourbon
        "extraction": {
            "brand_name": field("OLD TOM DISTILLERY"),
            "class_type": field("Kentucky Straight Bourbon Whiskey"),
            "alcohol_content": field("45% Alc./Vol. (90 Proof)"),
            "net_contents": field("750 mL"),
            "producer_name": field("Old Tom Distillery"),
            "producer_address": field("Bardstown, Kentucky 40004"),
            "country_of_origin": field(None),
            "health_warning": warning("Government Warning:"),
            "image_quality": {"readable": True, "issues": []},
        },
    },
    {
        "id": "old-tom-abv-mismatch",
        "title": "Old Tom Bourbon (ABV mismatch)",
        "description": "Label says 40% / 80 proof while the application says 45%. Expected: request correction.",
        "expected": "request_correction",
        "brand_lines": ["OLD TOM", "DISTILLERY"],
        "class_line": "Kentucky Straight Bourbon Whiskey",
        "tagline": "Aged Six Years in New Charred Oak",
        "alcohol_line": "40% Alc./Vol. (80 Proof)",
        "net_line": "750 mL",
        "producer_lines": [
            "Distilled and Bottled by Old Tom Distillery",
            "Bardstown, Kentucky 40004",
        ],
        "warning_heading": "GOVERNMENT WARNING:",
        "application": None,
        "extraction": {
            "brand_name": field("OLD TOM DISTILLERY"),
            "class_type": field("Kentucky Straight Bourbon Whiskey"),
            "alcohol_content": field("40% Alc./Vol. (80 Proof)"),
            "net_contents": field("750 mL"),
            "producer_name": field("Old Tom Distillery"),
            "producer_address": field("Bardstown, Kentucky 40004"),
            "country_of_origin": field(None),
            "health_warning": warning("GOVERNMENT WARNING:"),
            "image_quality": {"readable": True, "issues": []},
        },
    },
    {
        "id": "stones-throw-wine",
        "title": "Stone's Throw Cabernet (case difference)",
        "description": "Label prints STONE'S THROW, application says Stone's Throw. Dave's example; expected approve.",
        "expected": "approve",
        "paper": "#fbf8f1",
        "brand_lines": ["STONE'S THROW"],
        "class_line": "Cabernet Sauvignon",
        "tagline": "Napa Valley  ·  2021 Estate Bottled",
        "alcohol_line": "14.5% Alc. by Vol.",
        "net_line": "750 mL",
        "producer_lines": [
            "Produced and Bottled by Stone's Throw Vineyards",
            "St. Helena, California",
        ],
        "warning_heading": "GOVERNMENT WARNING:",
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
            "image_quality": {"readable": True, "issues": []},
        },
    },
    {
        "id": "glen-moray-scotch",
        "title": "Glen Aldie Scotch (import)",
        "description": "Imported spirit with 'Product of Scotland'. Application lists United Kingdom. Expected approve.",
        "expected": "approve",
        "paper": "#f3efe4",
        "brand_lines": ["GLEN ALDIE"],
        "class_line": "Single Malt Scotch Whisky",
        "tagline": "Aged 12 Years",
        "alcohol_line": "43% Alc./Vol. (86 Proof)",
        "net_line": "750 mL",
        "producer_lines": [
            "Distilled and Bottled by Glen Aldie Distillers, Speyside, Scotland",
            "Imported by Caledonia Imports, Newark, NJ",
        ],
        "origin_lines": ["PRODUCT OF SCOTLAND"],
        "warning_heading": "GOVERNMENT WARNING:",
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
            "image_quality": {"readable": True, "issues": []},
        },
    },
    {
        "id": "harbor-light-ipa-missing-warning",
        "title": "Harbor Light IPA (no warning)",
        "description": "Malt beverage with no health warning statement at all. Expected: request correction.",
        "expected": "request_correction",
        "paper": "#eef3f6",
        "brand_lines": ["HARBOR LIGHT"],
        "class_line": "India Pale Ale",
        "tagline": "Brewed with Citra and Mosaic Hops",
        "alcohol_line": "6.8% ALC/VOL",
        "net_line": "12 FL OZ (355 mL)",
        "producer_lines": [
            "Brewed and Canned by Harbor Light Brewing Co.",
            "Portland, Maine 04101",
        ],
        "warning_heading": None,
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
            "image_quality": {"readable": True, "issues": []},
        },
    },
    {
        "id": "old-tom-angled-photo",
        "title": "Old Tom Bourbon (angled photo)",
        "description": "The clean label photographed slightly rotated; the demo extractor reads it with lower confidence. Expected: needs review.",
        "expected": "needs_review",
        "rotate": 7,
        "brand_lines": ["OLD TOM", "DISTILLERY"],
        "class_line": "Kentucky Straight Bourbon Whiskey",
        "tagline": "Aged Six Years in New Charred Oak",
        "alcohol_line": "45% Alc./Vol. (90 Proof)",
        "net_line": "750 mL",
        "producer_lines": [
            "Distilled and Bottled by Old Tom Distillery",
            "Bardstown, Kentucky 40004",
        ],
        "warning_heading": "GOVERNMENT WARNING:",
        "application": None,
        "extraction": {
            "brand_name": field("OLD TOM DISTILLERY", 0.9),
            "class_type": field("Kentucky Straight Bourbon Whiskey", 0.85),
            "alcohol_content": field("45% Alc./Vol. (90 Proof)", 0.8),
            "net_contents": field("750 mL", 0.85),
            "producer_name": field("Old Tom Distillery", 0.7),
            "producer_address": field("Bardstown, Kentucky 40004", 0.55),
            "country_of_origin": field(None),
            "health_warning": {
                **warning("GOVERNMENT WARNING:", bold=True),
                "heading_bold": None,
                "confidence": 0.7,
            },
            "image_quality": {
                "readable": True,
                "issues": [
                    "Label is photographed at an angle; small text near the bottom is slightly skewed."
                ],
            },
        },
    },
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    base_application = SAMPLES[0]["application"]
    for sample in SAMPLES:
        image = render(sample)
        filename = f"{sample['id']}.png"
        image.save(OUT / filename, format="PNG", optimize=True)
        manifest.append(
            {
                "id": sample["id"],
                "title": sample["title"],
                "description": sample["description"],
                "file": filename,
                "expected": sample["expected"],
                "application": sample["application"] or base_application,
                "extraction": sample["extraction"],
            }
        )
        print(f"wrote {filename}")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote manifest.json ({len(manifest)} samples)")


if __name__ == "__main__":
    main()
