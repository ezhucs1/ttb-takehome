"""Vision-model extractor backed by the Anthropic API.

One request does the work of OCR plus classification: the model reads the label
panels, transcribes each required field verbatim, judges the warning heading's
capitalization and weight, and reports image-quality problems.

The model is asked for a JSON object in the shape of ``LabelExtraction`` and the reply
is validated client-side. The API's schema-constrained mode (``output_format``) is
available behind ``LABELVERIFY_STRUCTURED_OUTPUT=true`` but is off by default: it
compiles each new schema into a grammar on first use, and that compile step, which
re-runs whenever the schema changes, can take longer than a read is allowed to.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Sequence

import anthropic

from ..models import LabelExtraction
from .base import ExtractionError, Panel

# Measured on the angled-photo sample from a home connection: Opus 5.5 read it correctly in
# 6.0 s, Sonnet 5.5 in 4.1 s (median of 3). The brief's budget is 5 s, so Sonnet is the default.
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_TIMEOUT_SECONDS = 20.0

SYSTEM_PROMPT = """You are assisting a TTB labeling specialist. You will be shown one or more images of the same alcohol beverage product's labels: typically the front label, and sometimes the back or neck label, as artwork or as photographs of the container. Read every image carefully and report exactly what is printed, combining the panels into one answer.

Rules:
- Transcribe text exactly as printed, including capitalization, punctuation, and spelling mistakes. Never correct or complete text.
- Each field is reported once. If it appears on more than one panel, use the most legible instance.
- If a field is not visible on any panel, set its value to null and confidence to 0.
- Confidence is your certainty that the transcription is exactly right: 1.0 for crisp, unambiguous text; around 0.5 when glare, angle, or blur make characters uncertain; lower when guessing.
- brand_name is the product's brand as displayed, not the producer's company name unless they are the same.
- class_type is the class or type designation, for example "Kentucky Straight Bourbon Whiskey", "Cabernet Sauvignon", "India Pale Ale".
- alcohol_content is the full alcohol statement as printed, for example "45% Alc./Vol. (90 Proof)".
- net_contents is the volume statement as printed, for example "750 mL".
- producer_name and producer_address come from the "Distilled by", "Bottled by", "Produced by", "Brewed by", or "Imported by" statement.
- country_of_origin is the "Product of ..." or "Imported from ..." statement, or null if none.
- sulfite_declaration is the sulfite statement as printed, for example "Contains Sulfites", or null if none.
- qualifying_phrase is the wording that introduces the producer's name, exactly as printed: "Distilled and Bottled by", "Produced and Bottled by", "Brewed by", "Imported by", and so on; null if the name has no such phrase.
- importer_statement is the "Imported by ..." statement naming the U.S. importer, as printed, or null.
- age_statement is any statement of age as printed, such as "Aged 6 Years" or "12 Years Old"; null if none.
- bottled_in_bond_claim is "Bottled in Bond" or "Bonded" as printed when the label makes that claim; null otherwise.
- blend_percentage is a percentage statement about a blend's components as printed, such as "51% Straight Bourbon Whiskey"; null if none.
- appellation (wine) is the appellation of origin printed as the wine's origin, such as "Napa Valley", "Sonoma Coast", or "California", usually near the brand or vintage; it is not the city and state in the bottler's address. Null if none.
- vintage_year (wine) is the vintage year as printed, such as "2021"; null if none.
- estate_bottled_claim (wine) is "Estate Bottled" as printed when the label makes that claim; null otherwise.
- strength_claim (malt beverages) is any wording that emphasizes alcoholic strength as printed, such as "Extra Strength", "Strong", "High Test", or "Full Strength", wherever it appears on the label; null if none.
- product_category is your judgment of which TTB class the product is, from every cue (the class/type words, "Distilled by" or "Brewed by", proof, vintage, grape variety, "Contains sulfites"): exactly one of "distilled_spirits", "wine", or "malt_beverage", with your confidence; null if the label gives no basis.
- health_warning.text must be the complete Government Health Warning Statement transcribed verbatim starting at the words "GOVERNMENT WARNING", preserving the capitalization used on the label.
- health_warning.heading_all_caps is true only if the words GOVERNMENT WARNING are printed entirely in capital letters.
- health_warning.heading_bold is true only if that heading is printed noticeably bolder than the sentences that follow it. Use null if you cannot tell.
- image_quality.readable is false when substantial parts of the label text cannot be read. List concrete issues such as "glare across the bottom third of the front label" or "back label photographed at a steep angle"."""

# The reply shape, spelled out once so the model does not have to infer it from field names.
_FIELD = '{"value": "text as printed or null", "confidence": 0.0}'
_FIELD_KEYS = (
    "brand_name",
    "class_type",
    "alcohol_content",
    "net_contents",
    "producer_name",
    "producer_address",
    "country_of_origin",
    "sulfite_declaration",
    "qualifying_phrase",
    "importer_statement",
    "age_statement",
    "bottled_in_bond_claim",
    "blend_percentage",
    "appellation",
    "vintage_year",
    "estate_bottled_claim",
    "strength_claim",
    "product_category",
)
JSON_SHAPE = (
    "{"
    + ", ".join(f'"{key}": {_FIELD}' for key in _FIELD_KEYS)
    + ', "health_warning": {"present": true, "text": "verbatim statement or null", '
    '"heading_all_caps": true, "heading_bold": true, "confidence": 0.0}'
    ', "image_quality": {"readable": true, "issues": ["short note"]}'
    "}"
)

OUTPUT_INSTRUCTIONS = f"""

Output:
Reply with one JSON object and nothing else: no prose, no code fences. Use exactly these keys and this shape, with null for anything not printed and booleans as true/false (heading_bold may be null):
{JSON_SHAPE}"""

USER_PROMPT = "Extract the required TTB label fields from {what}."


def parse_label_json(text: str) -> LabelExtraction:
    """Validate the model's reply; tolerate code fences or a sentence around the object."""
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`")
        if body.startswith("json"):
            body = body[4:]
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end == -1:
        raise ExtractionError("The model's reply was not a JSON object.")
    try:
        return LabelExtraction.model_validate_json(body[start : end + 1])
    except ValueError as exc:  # pydantic's ValidationError and JSONDecodeError both are one
        raise ExtractionError("The model's reply was not valid label JSON.") from exc


def supports_effort(model: str) -> bool:
    """Haiku 4.5 rejects ``output_config.effort``; the Sonnet and Opus lines accept it."""
    return "haiku" not in model.lower()


class ClaudeExtractor:
    name = "claude"

    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        *,
        model: str | None = None,
        timeout: float | None = None,
        effort: str = "low",
        structured_output: bool | None = None,
    ):
        self.model = model or os.environ.get("LABELVERIFY_MODEL", DEFAULT_MODEL)
        self.timeout = timeout or float(
            os.environ.get("LABELVERIFY_EXTRACT_TIMEOUT", DEFAULT_TIMEOUT_SECONDS)
        )
        self.effort = effort
        self.structured_output = (
            structured_output
            if structured_output is not None
            else os.environ.get("LABELVERIFY_STRUCTURED_OUTPUT", "").strip().lower()
            in {"1", "true", "yes"}
        )
        self._client = client
        self.last_usage: dict[str, int] = {}
        self.last_request_id: str | None = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise ExtractionError("ANTHROPIC_API_KEY is not set.")
            self._client = anthropic.Anthropic(timeout=self.timeout, max_retries=1)
        return self._client

    def build_messages(self, panels: Sequence[Panel]) -> list[anthropic.types.MessageParam]:
        content: list[dict] = []
        for n, (image, media_type) in enumerate(panels, start=1):
            if len(panels) > 1:
                content.append({"type": "text", "text": f"Label image {n} of {len(panels)}:"})
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64.standard_b64encode(image).decode("ascii"),
                    },
                }
            )
        what = "this label image" if len(panels) == 1 else f"these {len(panels)} label images"
        content.append({"type": "text", "text": USER_PROMPT.format(what=what)})
        return [{"role": "user", "content": content}]

    def extract(self, image: bytes, media_type: str) -> LabelExtraction:
        return self.extract_panels([(image, media_type)])

    def timeout_for(self, panel_count: int) -> float:
        """Each extra panel adds half the base time: a front-and-back read gets 1.5x."""
        return self.timeout * (1 + 0.5 * max(panel_count - 1, 0))

    def extract_panels(self, panels: Sequence[Panel]) -> LabelExtraction:
        if not panels:
            raise ExtractionError("No label images were provided.")
        timeout = self.timeout_for(len(panels))
        request: dict = dict(
            model=self.model,
            max_tokens=8192,
            timeout=timeout,
            system=SYSTEM_PROMPT if self.structured_output else SYSTEM_PROMPT + OUTPUT_INSTRUCTIONS,
            messages=self.build_messages(panels),
        )
        if self.structured_output:
            request["output_format"] = LabelExtraction
        if supports_effort(self.model):
            request["output_config"] = {"effort": self.effort}
        try:
            if self.structured_output:
                response = self.client.messages.parse(**request)
            else:
                response = self.client.messages.create(**request)
        except anthropic.APITimeoutError as exc:
            raise ExtractionError(
                f"The model did not respond within {timeout:.0f} s for {len(panels)} "
                f"image{'s' if len(panels) != 1 else ''}. Try again, or raise "
                "LABELVERIFY_EXTRACT_TIMEOUT."
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ExtractionError("Could not reach the model API.") from exc
        except anthropic.AuthenticationError as exc:
            raise ExtractionError("The API key was rejected. Check ANTHROPIC_API_KEY.") from exc
        except anthropic.APIStatusError as exc:
            raise ExtractionError(f"Model API error ({exc.status_code}): {exc.message}") from exc

        self._remember(response)
        if response.stop_reason == "refusal":
            raise ExtractionError("The model declined to process this image.")
        if response.stop_reason == "max_tokens":
            raise ExtractionError("The model returned an incomplete result.")
        if self.structured_output:
            if response.parsed_output is None:
                raise ExtractionError("The model returned an incomplete result.")
            return response.parsed_output
        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        return parse_label_json(text)

    def _remember(self, response) -> None:
        """Keep the last call's token counts and request id for the CLI's timing report."""
        usage = getattr(response, "usage", None)
        self.last_usage = (
            {key: int(getattr(usage, key, 0) or 0) for key in ("input_tokens", "output_tokens")}
            if usage is not None
            else {}
        )
        self.last_request_id = getattr(response, "_request_id", None)
