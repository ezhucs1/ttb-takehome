"""Vision-model extractor backed by the Anthropic API.

One request does the work of OCR plus classification: the model reads the label,
transcribes each required field verbatim, judges the warning heading's capitalization
and weight, and reports image-quality problems. The response is constrained to the
``LabelExtraction`` schema so no free-text parsing is needed.
"""

from __future__ import annotations

import base64
import os

import anthropic

from ..models import LabelExtraction
from .base import ExtractionError

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_TIMEOUT_SECONDS = 20.0

SYSTEM_PROMPT = """You are assisting a TTB labeling specialist. You will be shown a photo or artwork of an alcohol beverage label. Read it carefully and report exactly what is printed.

Rules:
- Transcribe text exactly as printed, including capitalization, punctuation, and spelling mistakes. Never correct or complete text.
- If a field is not visible on the label, set its value to null and confidence to 0.
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
- image_quality.readable is false when substantial parts of the label text cannot be read. List concrete issues such as "glare across the bottom third" or "photographed at a steep angle"."""

USER_PROMPT = "Extract the required TTB label fields from this label image."


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

    def build_messages(self, image: bytes, media_type: str) -> list[anthropic.types.MessageParam]:
        encoded = base64.standard_b64encode(image).decode("ascii")
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": encoded},
                    },
                    {"type": "text", "text": USER_PROMPT},
                ],
            }
        ]

    def extract(self, image: bytes, media_type: str) -> LabelExtraction:
        try:
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                messages=self.build_messages(image, media_type),
                output_format=LabelExtraction,
                output_config={"effort": self.effort},
            )
        except anthropic.APITimeoutError as exc:
            raise ExtractionError("The model did not respond within the time limit.") from exc
        except anthropic.APIConnectionError as exc:
            raise ExtractionError("Could not reach the model API.") from exc
        except anthropic.APIStatusError as exc:
            raise ExtractionError(f"Model API error ({exc.status_code}).") from exc

        if response.stop_reason == "refusal":
            raise ExtractionError("The model declined to process this image.")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise ExtractionError("The model returned an incomplete result.")
        return response.parsed_output
