"""Vision-model extractor backed by the Anthropic API.

One request does the work of OCR plus classification: the model reads the label
panels, transcribes each required field verbatim, judges the warning heading's
capitalization and weight, and reports image-quality problems. The response is
constrained to the ``LabelExtraction`` schema so no free-text parsing is needed.
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
- health_warning.text must be the complete Government Health Warning Statement transcribed verbatim starting at the words "GOVERNMENT WARNING", preserving the capitalization used on the label.
- health_warning.heading_all_caps is true only if the words GOVERNMENT WARNING are printed entirely in capital letters.
- health_warning.heading_bold is true only if that heading is printed noticeably bolder than the sentences that follow it. Use null if you cannot tell.
- image_quality.readable is false when substantial parts of the label text cannot be read. List concrete issues such as "glare across the bottom third of the front label" or "back label photographed at a steep angle"."""

USER_PROMPT = "Extract the required TTB label fields from {what}."


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
    ):
        self.model = model or os.environ.get("LABELVERIFY_MODEL", DEFAULT_MODEL)
        self.timeout = timeout or float(
            os.environ.get("LABELVERIFY_EXTRACT_TIMEOUT", DEFAULT_TIMEOUT_SECONDS)
        )
        self.effort = effort
        self._client = client

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

    def extract_panels(self, panels: Sequence[Panel]) -> LabelExtraction:
        if not panels:
            raise ExtractionError("No label images were provided.")
        request: dict = dict(
            model=self.model,
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            messages=self.build_messages(panels),
            output_format=LabelExtraction,
        )
        if supports_effort(self.model):
            request["output_config"] = {"effort": self.effort}
        try:
            response = self.client.messages.parse(**request)
        except anthropic.APITimeoutError as exc:
            raise ExtractionError("The model did not respond within the time limit.") from exc
        except anthropic.APIConnectionError as exc:
            raise ExtractionError("Could not reach the model API.") from exc
        except anthropic.AuthenticationError as exc:
            raise ExtractionError("The API key was rejected. Check ANTHROPIC_API_KEY.") from exc
        except anthropic.APIStatusError as exc:
            raise ExtractionError(f"Model API error ({exc.status_code}): {exc.message}") from exc

        if response.stop_reason == "refusal":
            raise ExtractionError("The model declined to process this image.")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise ExtractionError("The model returned an incomplete result.")
        return response.parsed_output
