"""Vision-model extractor backed by the Google Gemini API.

Same job as the Claude extractor, through Gemini's ``generateContent`` endpoint with a
JSON response schema. Implemented against the REST API with the standard library so it
adds no dependency; the free tier is enough to evaluate accuracy and latency.

Configuration:
    GEMINI_API_KEY                   required
    LABELVERIFY_GEMINI_MODEL         default gemini-2.5-flash
    LABELVERIFY_GEMINI_THINKING      thinking budget for 2.5 models (default 0 = off, fastest)
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any

from ..models import LabelExtraction
from .base import ExtractionError, Panel
from .claude import SYSTEM_PROMPT, USER_PROMPT

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_TIMEOUT_SECONDS = 30.0
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_RETRY_STATUSES = {429, 503}


def to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert a pydantic JSON schema to the subset Gemini's response_schema accepts.

    Gemini takes an OpenAPI-style schema: no ``$ref``/``$defs``, ``anyOf`` with null becomes
    ``nullable``, and ``title``/``default``/``additionalProperties`` are dropped.
    """
    defs = schema.get("$defs", {})

    def convert(node: dict[str, Any]) -> dict[str, Any]:
        if "$ref" in node:
            target = defs[node["$ref"].rsplit("/", 1)[-1]]
            merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
            return convert(merged)
        out: dict[str, Any] = {}
        if "anyOf" in node:
            options = [o for o in node["anyOf"] if o.get("type") != "null"]
            nullable = len(options) < len(node["anyOf"])
            base = convert(options[0]) if options else {"type": "string"}
            if nullable:
                base["nullable"] = True
            if "description" in node:
                base["description"] = node["description"]
            return base
        for key in ("type", "description", "enum", "format"):
            if key in node:
                out[key] = node[key]
        if "properties" in node:
            out["properties"] = {name: convert(prop) for name, prop in node["properties"].items()}
            # Every property is listed as required so the model never omits a field;
            # nullable fields carry null instead of being left out.
            out["required"] = list(node["properties"].keys())
        if "items" in node:
            out["items"] = convert(node["items"])
        return out

    return convert(schema)


class GeminiExtractor:
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        transport=None,
    ):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.model = model or os.environ.get("LABELVERIFY_GEMINI_MODEL", DEFAULT_MODEL)
        self.timeout = timeout or float(
            os.environ.get("LABELVERIFY_EXTRACT_TIMEOUT", DEFAULT_TIMEOUT_SECONDS)
        )
        self.thinking_budget = int(os.environ.get("LABELVERIFY_GEMINI_THINKING", "0"))
        self._transport = transport or _http_post  # injectable for tests
        self._schema = to_gemini_schema(LabelExtraction.model_json_schema())

    def build_request(self, panels: Sequence[Panel]) -> dict[str, Any]:
        parts: list[dict[str, Any]] = []
        for n, (image, media_type) in enumerate(panels, start=1):
            if len(panels) > 1:
                parts.append({"text": f"Label image {n} of {len(panels)}:"})
            parts.append(
                {
                    "inline_data": {
                        "mime_type": media_type,
                        "data": base64.standard_b64encode(image).decode("ascii"),
                    }
                }
            )
        what = "this label image" if len(panels) == 1 else f"these {len(panels)} label images"
        parts.append({"text": USER_PROMPT.format(what=what)})
        config: dict[str, Any] = {
            "response_mime_type": "application/json",
            "response_schema": self._schema,
            "temperature": 0,
            "max_output_tokens": 8192,
        }
        if self.model.startswith("gemini-2.5"):
            config["thinking_config"] = {"thinking_budget": self.thinking_budget}
        return {
            "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": config,
        }

    def extract(self, image: bytes, media_type: str) -> LabelExtraction:
        return self.extract_panels([(image, media_type)])

    def extract_panels(self, panels: Sequence[Panel]) -> LabelExtraction:
        if not panels:
            raise ExtractionError("No label images were provided.")
        if not self.api_key:
            raise ExtractionError("GEMINI_API_KEY is not set.")
        payload = self.build_request(panels)
        response = call_gemini(
            self.model, self.api_key, payload, timeout=self.timeout, transport=self._transport
        )
        text = _response_text(response)
        try:
            return LabelExtraction.model_validate_json(text)
        except ValueError as exc:
            raise ExtractionError(
                f"Gemini returned JSON that did not match the schema: {exc}"
            ) from exc


def generate_text(
    prompt: str, *, system: str, model: str | None = None, timeout: float = 20.0
) -> str:
    """Plain text generation, used for rewriting correction notices."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise ExtractionError("GEMINI_API_KEY is not set.")
    model = model or os.environ.get("LABELVERIFY_GEMINI_MODEL", DEFAULT_MODEL)
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "max_output_tokens": 2048},
    }
    return _response_text(call_gemini(model, api_key, payload, timeout=timeout)).strip()


# --------------------------------------------------------------------------- transport


def call_gemini(
    model: str, api_key: str, payload: dict[str, Any], *, timeout: float, transport=None
) -> dict[str, Any]:
    """POST to generateContent with one retry on rate limiting or overload."""
    transport = transport or _http_post
    url = ENDPOINT.format(model=model)
    for attempt in (1, 2):
        try:
            return transport(url, api_key, payload, timeout)
        except _HttpStatus as exc:
            if exc.status in _RETRY_STATUSES and attempt == 1:
                time.sleep(2.0)
                continue
            raise ExtractionError(_describe_status(exc)) from exc
        except TimeoutError as exc:
            raise ExtractionError("Gemini did not respond within the time limit.") from exc
        except urllib.error.URLError as exc:
            raise ExtractionError(f"Could not reach the Gemini API: {exc.reason}") from exc
    raise ExtractionError("Gemini is rate limiting this key; try again in a minute.")


class _HttpStatus(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body


def _http_post(url: str, api_key: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise _HttpStatus(exc.code, exc.read().decode(errors="replace")) from exc


def _describe_status(exc: _HttpStatus) -> str:
    detail = ""
    try:
        detail = json.loads(exc.body).get("error", {}).get("message", "")
    except (ValueError, AttributeError):
        detail = exc.body[:200]
    if exc.status in (401, 403) or "api key" in detail.lower():
        return f"The Gemini API key was rejected. Check GEMINI_API_KEY. {detail}".strip()
    if exc.status == 429:
        return f"Gemini free-tier rate limit reached; wait a minute and retry. {detail}".strip()
    if exc.status == 404:
        return f"Gemini model not found; check LABELVERIFY_GEMINI_MODEL. {detail}".strip()
    return f"Gemini API error ({exc.status}): {detail}".strip()


def _response_text(response: dict[str, Any]) -> str:
    feedback = response.get("promptFeedback", {})
    if feedback.get("blockReason"):
        raise ExtractionError(f"Gemini declined to process this image ({feedback['blockReason']}).")
    candidates = response.get("candidates") or []
    if not candidates:
        raise ExtractionError("Gemini returned no result.")
    candidate = candidates[0]
    reason = candidate.get("finishReason", "STOP")
    parts = candidate.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts)
    if reason == "MAX_TOKENS":
        raise ExtractionError("Gemini returned an incomplete result (output limit reached).")
    if reason not in ("STOP", "FINISH_REASON_UNSPECIFIED") and not text:
        raise ExtractionError(f"Gemini stopped early ({reason}).")
    if not text:
        raise ExtractionError("Gemini returned an empty result.")
    return text
