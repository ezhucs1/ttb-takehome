"""Command-line runner for the verification engine.

Useful for measuring real latency and for checking a label without the web app:

    python -m labelverify.cli verify label.jpg --application application.json
    python -m labelverify.cli verify label.jpg --application application.json --extractor tesseract
    python -m labelverify.cli extract label.jpg

``application.json`` holds the ``ApplicationData`` fields, for example:

    {"beverage_type": "distilled_spirits", "brand_name": "OLD TOM DISTILLERY",
     "class_type": "Kentucky Straight Bourbon Whiskey",
     "alcohol_content": "45% Alc./Vol. (90 Proof)", "net_contents": "750 mL"}
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .engine.extractors import ExtractionError, get_extractor
from .engine.models import ApplicationData, Verdict
from .engine.preprocess import UnreadableImageError, prepare_image
from .engine.verify import default_fallback, run_verification

_ICONS = {
    Verdict.MATCH: "PASS",
    Verdict.NEEDS_REVIEW: "REVIEW",
    Verdict.MISMATCH: "FAIL",
    Verdict.NOT_APPLICABLE: "N/A",
}


def _read_file(path_text: str, what: str) -> bytes:
    path = Path(path_text).expanduser()
    if not path.is_file():
        sys.exit(
            f"error: {what} not found: {path}\n"
            "hint: pass a real image path, e.g. labelverify/samples/old-tom-bourbon.jpg"
        )
    return path.read_bytes()


def _load_application(path_text: str) -> ApplicationData:
    return ApplicationData.model_validate_json(_read_file(path_text, "application file"))


def cmd_verify(args: argparse.Namespace) -> int:
    image = _read_file(args.image, "image")
    application = _load_application(args.application)
    extractor = get_extractor(args.extractor)
    fallback = default_fallback() if args.fallback else None
    try:
        result = run_verification(image, application, extractor=extractor, fallback=fallback)
    except UnreadableImageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ExtractionError as exc:
        print(f"error: extraction failed: {exc}", file=sys.stderr)
        return 3

    if args.json:
        print(result.model_dump_json(indent=2))
        return 0

    print(f"Recommendation: {result.recommendation.value.upper()}")
    print(
        f"Extractor: {result.extractor}   extraction {result.extraction_ms} ms   total {result.total_ms} ms"
    )
    print()
    for field in result.fields:
        print(f"[{_ICONS[field.verdict]:>6}] {field.label}")
        print(f"         application: {field.application_value or '(blank)'}")
        print(f"         label:       {field.label_value or '(not found)'}")
        print(f"         {field.reason}")
        for note in field.notes:
            print(f"         note: {note}")
    print()
    for line in result.summary:
        print(f"- {line}")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    image = _read_file(args.image, "image")
    extractor = get_extractor(args.extractor)
    try:
        prepared = prepare_image(image)
        started = time.perf_counter()
        extraction = extractor.extract(prepared.data, prepared.media_type)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
    except UnreadableImageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ExtractionError as exc:
        print(f"error: extraction failed: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(extraction.model_dump(mode="json"), indent=2))
    print(f"\n# {extractor.name}: {elapsed_ms} ms", file=sys.stderr)
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    """Time each extractor on the same image several times; prints per-run and median."""
    import statistics

    image = _read_file(args.image, "image")
    prepared = prepare_image(image)
    names = [n.strip() for n in args.extractors.split(",") if n.strip()]
    print(f"image: {args.image} ({prepared.width}x{prepared.height} after preprocessing)")
    print(
        f"{'extractor':<12} {'model':<24} {'runs':>4} {'median ms':>10} {'min ms':>8} {'max ms':>8}"
    )
    for name in names:
        extractor = get_extractor(name)
        times: list[int] = []
        failures: list[str] = []
        for _ in range(args.runs):
            started = time.perf_counter()
            try:
                extractor.extract(prepared.data, prepared.media_type)
                times.append(int((time.perf_counter() - started) * 1000))
            except ExtractionError as exc:
                failures.append(str(exc))
        model = getattr(extractor, "model", "")
        if times:
            print(
                f"{name:<12} {model:<24} {len(times):>4} {int(statistics.median(times)):>10} "
                f"{min(times):>8} {max(times):>8}"
            )
        if failures:
            print(f"{name:<12} {len(failures)} failed run(s): {failures[-1]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(prog="labelverify", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    verify_p = sub.add_parser("verify", help="Compare a label image against application data.")
    verify_p.add_argument("image")
    verify_p.add_argument(
        "--application", required=True, help="Path to an ApplicationData JSON file."
    )
    verify_p.add_argument(
        "--extractor",
        default=None,
        help="claude, gemini, tesseract, or demo (default: by configured key).",
    )
    verify_p.add_argument(
        "--fallback",
        action="store_true",
        help="Fall back to tesseract if the primary extractor fails.",
    )
    verify_p.add_argument("--json", action="store_true", help="Print the full result as JSON.")
    verify_p.set_defaults(func=cmd_verify)

    extract_p = sub.add_parser("extract", help="Print the raw extraction for a label image.")
    extract_p.add_argument("image")
    extract_p.add_argument("--extractor", default=None)
    extract_p.set_defaults(func=cmd_extract)

    bench_p = sub.add_parser("bench", help="Time extractors on one image and print medians.")
    bench_p.add_argument("image")
    bench_p.add_argument("--extractors", default="claude,gemini", help="Comma-separated names.")
    bench_p.add_argument("--runs", type=int, default=3)
    bench_p.set_defaults(func=cmd_bench)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
