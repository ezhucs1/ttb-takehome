# LabelVerify

AI-assisted alcohol label verification for TTB COLA review. An applicant uploads label
artwork and application data, the engine reads the label and compares it field by field
against the application, and a labeling specialist reviews the result and decides.

This repository currently contains the **verification engine and its tests**. The web
application (applicant and specialist workflows, batch upload) is built on top of it.

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv venv && uv pip install -e ".[dev]"
cp .env.example .env            # add ANTHROPIC_API_KEY for the vision extractor
.venv/bin/python -m pytest      # 123 tests, runs offline
```

Optional: install the `tesseract` binary (`apt install tesseract-ocr` or `brew install tesseract`)
to enable the local OCR fallback.

## Try the engine from the command line

```bash
# Compare a label image against application data
.venv/bin/python -m labelverify.cli verify label.jpg --application application.json

# Same, but fall back to local OCR if the model API is unreachable
.venv/bin/python -m labelverify.cli verify label.jpg --application application.json --fallback

# Just print what the extractor reads, with timing
.venv/bin/python -m labelverify.cli extract label.jpg
```

`application.json`:

```json
{
  "beverage_type": "distilled_spirits",
  "brand_name": "OLD TOM DISTILLERY",
  "class_type": "Kentucky Straight Bourbon Whiskey",
  "alcohol_content": "45% Alc./Vol. (90 Proof)",
  "net_contents": "750 mL",
  "producer_name": "Old Tom Distillery",
  "producer_address": "123 Barrel Lane, Bardstown, KY 40004",
  "is_import": false,
  "country_of_origin": ""
}
```

## How verification works

```
image bytes ──> preprocess ──> extractor ──> LabelExtraction ──┐
                (EXIF rotate,   (Claude vision                 ├──> compare ──> VerificationResult
                 resize 1500px)  or Tesseract)                 │    (rules,      (per-field verdicts,
                                       ApplicationData ────────┘     no model)    recommendation)
```

1. **Preprocess** (`engine/preprocess.py`): apply EXIF orientation, downscale to 1500 px on
   the long edge, re-encode as JPEG. Smaller uploads are the biggest latency win.
2. **Extract** (`engine/extractors/`): one vision-model call returns every required field as
   structured JSON, including a verbatim transcription of the health warning, whether its
   heading is all caps and bold, per-field confidence, and image-quality notes. A local
   Tesseract + regex extractor implements the same interface for environments that block
   outbound API traffic.
3. **Compare** (`engine/compare.py`, `engine/warning.py`): deterministic, unit-tested rules
   produce a verdict per field. No model is involved in the comparison.

| Field | Strategy | Example that matches |
| --- | --- | --- |
| Brand name, class/type, producer | Identical after normalization (case, punctuation, spacing, synonyms). Similar-but-different goes to a human. | `STONE'S THROW` = `Stone's Throw`; `Whisky` = `Whiskey` |
| Alcohol content | Parsed to % ABV; proof converted; label proof must equal 2 x ABV | `45% Alc./Vol. (90 Proof)` = `45` = `90 proof` |
| Net contents | Parsed to mL; standard-of-fill sizes noted | `750 mL` = `0.75 L` = `75 cL` |
| Country of origin | Required for imports only; aliases folded | `Product of Scotland` = `United Kingdom` |
| Health warning | Word for word against 27 CFR 16.21; `GOVERNMENT WARNING` must be all caps; bold is a visual judgment that goes to review when uncertain | exact statutory text |

Verdicts per field are **match**, **needs review**, **mismatch**, or **not applicable**.
A match from a low-confidence read is downgraded to needs review. The roll-up is
**approve**, **needs review**, or **request correction**. The engine never approves on its
own; the specialist always decides.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `LABELVERIFY_EXTRACTOR` | `claude` | `claude` or `tesseract` |
| `ANTHROPIC_API_KEY` | | Required for the `claude` extractor |
| `LABELVERIFY_MODEL` | `claude-opus-5-5` | Swap to `claude-haiku-4-5` if latency measures over budget |
| `LABELVERIFY_EXTRACT_TIMEOUT` | `20` | Seconds before an extraction call is abandoned |

## Layout

```
labelverify/
  cli.py                  command-line runner
  engine/
    models.py             ApplicationData, LabelExtraction, VerificationResult
    normalize.py          text, ABV, volume, address, country normalizers
    warning.py            health warning statement rules and word diff
    compare.py            per-field comparison rules and roll-up
    preprocess.py         image preparation
    verify.py             orchestration with timing and fallback
    extractors/           claude (vision model), tesseract (local OCR), fixture (tests)
tests/                    pytest suite, runs offline with a fake API client
```
