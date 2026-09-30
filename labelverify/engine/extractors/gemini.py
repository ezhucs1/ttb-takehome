"""Vision-model extractor backed by the Google Gemini API.

Same job as the Claude extractor, through Gemini's ``generateContent`` endpoint with a
JSON response schema. Implemented against the REST API with the standard library so it
adds no dependency; the free tier is enough to evaluate accuracy and latency.

Model selection is deliberately not hardcoded: Google retires model names for new keys,
so by default the extractor asks the models endpoint for the newest stable Flash model
that supports generation. A configured model that comes back "not found" falls back to
the replacement Google names in its error, or to discovery, once.

Configuration:
    GEMINI_API_KEY                   required
    LABELVERIFY_GEMINI_MODEL         a model name, or blank / "auto" to discover (default)
    LABELVERIFY_GEMINI_THINKING      optional thinking budget for Gemini 2.5 models (0 = off)
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any

from ..models import LabelExtraction
from .base import ExtractionError, Panel
from .claude import SYSTEM_PROMPT, USER_PROMPT

log = logging.getLogger(__name__)

AUTO = "auto"
DEFAULT_TIMEOUT_SECONDS = 30.0
BASE = "https://generativelanguage.googleapis.com/v1beta"
ENDPOINT = BASE + "/models/{model}:generateContent"
MODELS_ENDPOINT = BASE + "/models?pageSize=200"
_RETRY_STATUSES = {429, 503}
# Free-tier keys see "high demand" 503s and per-minute 429s often; back off a few times
# before giving up. Total added wait is about nine seconds.
_BACKOFF_SECONDS = (1.5, 3.0, 4.5)
_SUGGESTED_MODEL_RE = re.compile(r"models/([a-z0-9.\-]+)")
_STABLE_FLASH_RE = re.compile(r"^gemini-(\d+)(?:\.(\d+))?-flash$")
_EXCLUDE_WORDS = ("lite", "image", "tts", "live", "audio", "embedding", "thinking", "exp")

_discovered: dict[str, str] = {}  # api key -> model, per process


def configured_model() -> str:
    return (os.environ.get("LABELVERIFY_GEMINI_MODEL") or AUTO).strip() or AUTO


# --------------------------------------------------------------------------- schema


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


# --------------------------------------------------------------------------- model discovery


def pick_model(models: list[dict[str, Any]]) -> str | None:
    """Choose the newest stable, full-size Flash model that supports generateContent.

    Preference order: ``gemini-<major>.<minor>-flash`` by version, then any other Flash
    variant that is not lite/preview-only for a different modality, then anything usable.
    """
    usable = [
        m["name"].removeprefix("models/")
        for m in models
        if "generateContent" in (m.get("supportedGenerationMethods") or [])
    ]

    def version(name: str) -> tuple[int, int]:
        match = _STABLE_FLASH_RE.match(name)
        return (int(match.group(1)), int(match.group(2) or 0)) if match else (-1, -1)

    stable = sorted((n for n in usable if _STABLE_FLASH_RE.match(n)), key=version, reverse=True)
    if stable:
        return stable[0]
    flash = [n for n in usable if "flash" in n and not any(w in n for w in _EXCLUDE_WORDS)]
    if flash:
        return sorted(flash, reverse=True)[0]
    return usable[0] if usable else None


def discover_model(api_key: str, *, timeout: float, list_transport=None) -> str:
    """Ask the models endpoint which model to use; cached per process and key."""
    if api_key in _discovered:
        return _discovered[api_key]
    list_transport = list_transport or _http_get
    try:
        listing = list_transport(MODELS_ENDPOINT, api_key, timeout)
    except _HttpStatus as exc:
        raise ExtractionError(f"Could not list Gemini models: {_describe_status(exc)}") from exc
    except (TimeoutError, urllib.error.URLError) as exc:
        raise ExtractionError(f"Could not reach the Gemini API to list models: {exc}") from exc
    model = pick_model(listing.get("models", []))
    if not model:
        raise ExtractionError("This Gemini key has no model that supports generateContent.")
    log.info("gemini: using discovered model %s", model)
    _discovered[api_key] = model
    return model


# --------------------------------------------------------------------------- extractor


class GeminiExtractor:
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        transport=None,
        list_transport=None,
    ):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.model = model or configured_model()
        self.timeout = timeout or float(
            os.environ.get("LABELVERIFY_EXTRACT_TIMEOUT", DEFAULT_TIMEOUT_SECONDS)
        )
        raw_budget = os.environ.get("LABELVERIFY_GEMINI_THINKING", "").strip()
        self.thinking_budget = int(raw_budget) if raw_budget else None
        self._transport = transport or _http_post  # injectable for tests
        self._list_transport = list_transport or _http_get
        self._schema = to_gemini_schema(LabelExtraction.model_json_schema())

    def resolve_model(self) -> str:
        if self.model == AUTO:
            self.model = discover_model(
                self.api_key, timeout=self.timeout, list_transport=self._list_transport
            )
        return self.model

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
        # Only Gemini 2.5 takes a thinking budget; newer generations use different knobs,
        # so nothing is sent unless explicitly configured for a 2.5 model.
        if self.thinking_budget is not None and self.model.startswith("gemini-2.5"):
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
        self.resolve_model()
        try:
            response = self._call(panels)
        except ModelNotFound as exc:
            replacement = exc.suggested or discover_model(
                self.api_key, timeout=self.timeout, list_transport=self._list_transport
            )
            if not replacement or replacement == self.model:
                raise
            log.warning("gemini: model %s unavailable, switching to %s", self.model, replacement)
            self.model = replacement
            response = self._call(panels)
        text = _response_text(response)
        try:
            return LabelExtraction.model_validate_json(text)
        except ValueError as exc:
            raise ExtractionError(
                f"Gemini returned JSON that did not match the schema: {exc}"
            ) from exc

    def _call(self, panels: Sequence[Panel]) -> dict[str, Any]:
        return call_gemini(
            self.model,
            self.api_key,
            self.build_request(panels),
            timeout=self.timeout,
            transport=self._transport,
        )


def generate_text(
    prompt: str, *, system: str, model: str | None = None, timeout: float = 20.0
) -> str:
    """Plain text generation, used for rewriting correction notices."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise ExtractionError("GEMINI_API_KEY is not set.")
    model = model or configured_model()
    if model == AUTO:
        model = discover_model(api_key, timeout=timeout)
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "max_output_tokens": 2048},
    }
    return _response_text(call_gemini(model, api_key, payload, timeout=timeout)).strip()


# --------------------------------------------------------------------------- transport


class ModelNotFound(ExtractionError):
    """The requested model is unavailable; ``suggested`` is Google's replacement, if named."""

    def __init__(self, message: str, suggested: str | None):
        super().__init__(message)
        self.suggested = suggested


def call_gemini(
    model: str, api_key: str, payload: dict[str, Any], *, timeout: float, transport=None
) -> dict[str, Any]:
    """POST to generateContent with one retry on rate limiting or overload."""
    transport = transport or _http_post
    url = ENDPOINT.format(model=model)
    for attempt in range(len(_BACKOFF_SECONDS) + 1):
        try:
            return transport(url, api_key, payload, timeout)
        except _HttpStatus as exc:
            if exc.status in _RETRY_STATUSES:
                if attempt < len(_BACKOFF_SECONDS):
                    log.info("gemini: %s, retrying in %.1fs", exc.status, _BACKOFF_SECONDS[attempt])
                    time.sleep(_BACKOFF_SECONDS[attempt])
                    continue
                raise ExtractionError(
                    f"Gemini is still unavailable after {len(_BACKOFF_SECONDS)} retries: "
                    f"{_describe_status(exc)}"
                ) from exc
            if exc.status == 404:
                raise ModelNotFound(_describe_status(exc), _suggested_model(exc, model)) from exc
            raise ExtractionError(_describe_status(exc)) from exc
        except TimeoutError as exc:
            raise ExtractionError("Gemini did not respond within the time limit.") from exc
        except urllib.error.URLError as exc:
            raise ExtractionError(f"Could not reach the Gemini API: {exc.reason}") from exc
    raise AssertionError("unreachable")  # every path above returns or raises


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
    return _send(request, timeout)


def _http_get(url: str, api_key: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"x-goog-api-key": api_key}, method="GET")
    return _send(request, timeout)


def _send(request: urllib.request.Request, timeout: float) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise _HttpStatus(exc.code, exc.read().decode(errors="replace")) from exc


def _error_detail(exc: _HttpStatus) -> str:
    try:
        return json.loads(exc.body).get("error", {}).get("message", "")
    except (ValueError, AttributeError):
        return exc.body[:200]


def _suggested_model(exc: _HttpStatus, current: str) -> str | None:
    """Google's not-found message often says 'use models/<replacement>'."""
    names = [n for n in _SUGGESTED_MODEL_RE.findall(_error_detail(exc)) if n != current]
    return names[0] if names else None


def _describe_status(exc: _HttpStatus) -> str:
    detail = _error_detail(exc)
    if exc.status in (401, 403) or "api key" in detail.lower():
        return f"The Gemini API key was rejected. Check GEMINI_API_KEY. {detail}".strip()
    if exc.status == 429:
        return f"Gemini free-tier rate limit reached; wait a minute and retry. {detail}".strip()
    if exc.status == 503:
        return f"Gemini is overloaded (high demand on the free tier). {detail}".strip()
    if exc.status == 404:
        return (
            "Gemini model not found. Leave LABELVERIFY_GEMINI_MODEL blank to pick one "
            f"automatically. {detail}"
        ).strip()
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
