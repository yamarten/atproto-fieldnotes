from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import time
import urllib.error
from pathlib import Path
from typing import Any

from . import http_transport


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LLM_PROMPT_VERSION = "project-triage-v1"
ENDPOINT_INCOMPATIBLE_HTTP_STATUSES = {400, 404, 405, 415, 422, 501}


class LLMTransportError(RuntimeError):
    pass


class LLMEndpointIncompatibleError(RuntimeError):
    pass


class LLMResponseError(ValueError):
    pass


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        match = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL)
        if match:
            return match.group(1).strip()
    return stripped


def normalize_llm_api_style(value: str | None) -> str:
    style = str(value or "").strip().lower()
    return style if style in {"auto", "chat", "responses", "completions"} else "auto"


def llm_endpoint_candidates(endpoint: str, api_style: str = "auto") -> list[tuple[str, str]]:
    normalized = endpoint.rstrip("/")
    style = normalize_llm_api_style(api_style)
    base = normalized
    if normalized.endswith("/v1/chat/completions"):
        base = normalized.removesuffix("/chat/completions")
    elif normalized.endswith("/chat/completions"):
        base = normalized.removesuffix("/chat/completions")
    elif normalized.endswith("/v1/responses"):
        base = normalized.removesuffix("/responses")
    elif normalized.endswith("/responses"):
        base = normalized.removesuffix("/responses")
    elif normalized.endswith("/v1/completions"):
        base = normalized.removesuffix("/completions")
    elif normalized.endswith("/completions"):
        base = normalized.removesuffix("/completions")
    elif not normalized.endswith("/v1"):
        base = f"{normalized}/v1"

    ordered_styles = {
        "responses": ["responses"],
        "chat": ["chat"],
        "completions": ["completions"],
        "auto": ["chat", "responses", "completions"],
    }[style]
    candidates: list[tuple[str, str]] = []
    for item in ordered_styles:
        if item == "chat":
            candidates.append((f"{base}/chat/completions", "chat"))
        elif item == "responses":
            candidates.append((f"{base}/responses", "responses"))
        elif item == "completions":
            candidates.append((f"{base}/completions", "completions"))
    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped


def extract_text_from_openai_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "output_text", "content", "value"):
            if key in value:
                extracted = extract_text_from_openai_content(value.get(key))
                if extracted:
                    return extracted
        return ""
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            extracted = extract_text_from_openai_content(item)
            if extracted:
                parts.append(extracted)
        if parts:
            return "\n".join(parts)
    return ""


def parse_openai_compatible_response(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("choices"), list) and payload["choices"]:
        first_choice = payload["choices"][0]
        message = first_choice.get("message")
        if isinstance(message, dict):
            extracted = extract_text_from_openai_content(message.get("content"))
            if extracted:
                return extracted
        if first_choice.get("text"):
            return str(first_choice["text"])
    output_text = extract_text_from_openai_content(payload.get("output_text"))
    if output_text:
        return output_text
    output = payload.get("output")
    extracted_output = extract_text_from_openai_content(output)
    if extracted_output:
        return extracted_output
    response = payload.get("response")
    extracted_response = extract_text_from_openai_content(response)
    if extracted_response:
        return extracted_response
    raise ValueError("endpoint response did not contain message content")


def request_json_post_raw(
    url: str,
    payload: Any,
    headers: dict[str, str] | None = None,
    *,
    timeout: float = 60,
    allow_blocked_hosts: bool = False,
) -> tuple[Any, str]:
    merged_headers = {"Content-Type": "application/json"}
    if headers:
        merged_headers.update(headers)
    _, body, _ = http_transport.request_bytes(
        url,
        headers=merged_headers,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        timeout=timeout,
        allow_blocked_hosts=allow_blocked_hosts,
    )
    text = body.decode("utf-8", errors="replace")
    return json.loads(text), text


def parse_completion_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("choices"), list) and payload["choices"]:
        first_choice = payload["choices"][0]
        if first_choice.get("text"):
            return str(first_choice["text"])
        message = first_choice.get("message")
        if isinstance(message, dict) and message.get("content"):
            return str(message["content"])
    raise ValueError("completion response did not contain text")


def normalize_llm_reasoning_effort(value: str | None) -> str:
    effort = str(value or "").strip().lower()
    if effort in {"none", "minimal", "low", "medium", "high", "on", "off"}:
        return effort
    if re.fullmatch(r"[a-z0-9_-]{1,32}", effort):
        return effort
    return ""


def llm_timeout_seconds() -> float:
    try:
        return max(0.1, float(os.getenv("ATPROTO_SURVEY_LLM_TIMEOUT", "300").strip() or "300"))
    except ValueError:
        return 300.0


def classify_llm_request_error(exc: Exception) -> str:
    message = str(exc).lower()
    if "atproto_survey_llm_command or atproto_survey_llm_endpoint is required" in message:
        return "unconfigured"
    if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) or "timed out" in message or "timeout" in message:
        return "timeout"
    if isinstance(exc, LLMEndpointIncompatibleError):
        return "endpoint_incompatible"
    if isinstance(exc, LLMResponseError):
        return "invalid_response"
    if isinstance(exc, LLMTransportError):
        return "transport_failure"
    if "http 429" in message:
        return "rate_limited"
    if "http " in message:
        return "http_error"
    if isinstance(exc, json.JSONDecodeError) or "json" in message:
        return "invalid_response"
    return "request_error"


def run_llm_with_metrics(
    prompt: str,
    *,
    purpose: str,
    requests: list[dict[str, Any]],
    **kwargs: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
    request_metrics: dict[str, Any] = {
        "purpose": purpose,
        "status": "failed",
        "error_category": "request_error",
        "prompt_chars": len(prompt),
        "prompt_bytes": len(prompt.encode("utf-8")),
        "response_bytes": 0,
        "timeout_seconds": llm_timeout_seconds(),
    }
    try:
        payload, meta = run_local_llm(prompt, **kwargs)
        request_metrics.update({
            "status": "success",
            "error_category": "",
            "response_bytes": len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
            "transport": str(meta.get("transport") or ""),
            "model": str(meta.get("model") or ""),
            "api_style": str(meta.get("api_style") or ""),
            "reasoning_effort": str(meta.get("reasoning_effort") or ""),
            "prompt_version": str(meta.get("prompt_version") or ""),
        })
        return payload, meta
    except Exception as exc:
        request_metrics["error_category"] = classify_llm_request_error(exc)
        raise
    finally:
        request_metrics["duration_ms"] = max(0, round((time.perf_counter() - started) * 1000))
        requests.append(request_metrics)


def run_local_llm_via_endpoint(
    prompt: str,
    endpoint: str,
    model: str,
    api_key: str,
    reasoning_effort: str = "",
    api_style: str = "auto",
) -> tuple[dict[str, Any], dict[str, Any]]:
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    llm_timeout = llm_timeout_seconds()
    normalized_effort = normalize_llm_reasoning_effort(reasoning_effort or os.getenv("ATPROTO_SURVEY_LLM_REASONING_EFFORT", ""))
    normalized_style = normalize_llm_api_style(api_style or os.getenv("ATPROTO_SURVEY_LLM_API_STYLE", "auto"))
    effective_style = "responses" if normalized_style == "auto" and normalized_effort else normalized_style
    endpoint_urls = llm_endpoint_candidates(endpoint, effective_style)
    chat_payloads = [
        {
            "model": model or "local-model",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        },
        {
            "model": model or "local-model",
            "messages": [{"role": "system", "content": "Return JSON only."}, {"role": "user", "content": prompt}],
            "temperature": 0.2,
        },
    ]
    completion_payload = {
        "model": model or "local-model",
        "prompt": prompt + "\n\nReturn JSON only.",
        "temperature": 0.2,
    }
    if normalized_effort:
        for payload in chat_payloads:
            payload["reasoning_effort"] = normalized_effort
    responses_payloads = [
        {
            "model": model or "local-model",
            "input": prompt + "\n\nReturn JSON only.",
            "temperature": 0.2,
        }
    ]
    if normalized_effort:
        for payload in responses_payloads:
            payload["reasoning"] = {"effort": normalized_effort}
    errors: list[str] = []

    for url, endpoint_kind in endpoint_urls:
        if endpoint_kind == "chat":
            payloads = chat_payloads
        elif endpoint_kind == "responses":
            payloads = responses_payloads
        else:
            payloads = [completion_payload]
        for payload in payloads:
            try:
                response_payload, _ = request_json_post_raw(
                    url,
                    payload,
                    headers=headers,
                    timeout=llm_timeout,
                    allow_blocked_hosts=True,
                )
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    body = ""
                detail = f"{url} -> HTTP {exc.code}: {body[:300].strip() or exc.reason}"
                if int(exc.code) in ENDPOINT_INCOMPATIBLE_HTTP_STATUSES:
                    errors.append(detail)
                    continue
                raise RuntimeError(detail) from exc
            except (TimeoutError, subprocess.TimeoutExpired):
                raise
            except json.JSONDecodeError as exc:
                raise LLMResponseError(f"{url} returned invalid JSON: {exc}") from exc
            except Exception as exc:
                raise LLMTransportError(f"{url} request failed: {exc}") from exc

            try:
                raw_content = (
                    parse_openai_compatible_response(response_payload)
                    if endpoint_kind in {"chat", "responses"}
                    else parse_completion_text(response_payload)
                )
                content = strip_json_fence(raw_content)
                parsed = json.loads(content)
            except Exception as exc:
                raise LLMResponseError(f"{url} returned unusable model content: {exc}") from exc
            meta = {
                "transport": "openai_compatible_endpoint",
                "endpoint": endpoint,
                "resolved_endpoint": url,
                "api_style": endpoint_kind,
                "requested_api_style": normalized_style,
                "model": model or "local-model",
                "reasoning_effort": normalized_effort,
                "prompt_version": os.getenv("ATPROTO_SURVEY_LLM_PROMPT_VERSION", DEFAULT_LLM_PROMPT_VERSION).strip(),
                "generated_at": iso_now(),
            }
            return parsed, meta

    joined_errors = " | ".join(errors[:6]) if errors else "unknown endpoint failure"
    raise LLMEndpointIncompatibleError(joined_errors)


def run_local_llm(
    prompt: str,
    llm_endpoint: str | None = None,
    llm_model: str | None = None,
    llm_api_key: str | None = None,
    llm_reasoning_effort: str | None = None,
    llm_api_style: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    command = os.getenv("ATPROTO_SURVEY_LLM_COMMAND", "").strip()
    normalized_effort = normalize_llm_reasoning_effort(llm_reasoning_effort or os.getenv("ATPROTO_SURVEY_LLM_REASONING_EFFORT", ""))
    normalized_api_style = normalize_llm_api_style(llm_api_style or os.getenv("ATPROTO_SURVEY_LLM_API_STYLE", "auto"))
    if command:
        env = dict(os.environ)
        if normalized_effort:
            env["ATPROTO_SURVEY_LLM_REASONING_EFFORT"] = normalized_effort
        env["ATPROTO_SURVEY_LLM_API_STYLE"] = normalized_api_style
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            shell=True,
            cwd=str(ROOT),
            timeout=llm_timeout_seconds(),
            env=env,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            raise LLMTransportError(f"LLM command failed with exit code {completed.returncode}: {stderr or 'no stderr'}")
        stdout = strip_json_fence(completed.stdout)
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise LLMResponseError(f"LLM command returned invalid JSON: {exc}") from exc
        meta = {
            "transport": "command",
            "command": command,
            "model": llm_model or os.getenv("ATPROTO_SURVEY_LLM_MODEL", "").strip(),
            "api_style": normalized_api_style,
            "reasoning_effort": normalized_effort,
            "prompt_version": os.getenv("ATPROTO_SURVEY_LLM_PROMPT_VERSION", DEFAULT_LLM_PROMPT_VERSION).strip(),
            "generated_at": iso_now(),
        }
        return payload, meta

    endpoint = (llm_endpoint or os.getenv("ATPROTO_SURVEY_LLM_ENDPOINT", "")).strip()
    model = (llm_model or os.getenv("ATPROTO_SURVEY_LLM_MODEL", "")).strip()
    api_key = (llm_api_key or os.getenv("ATPROTO_SURVEY_LLM_API_KEY", "")).strip()
    if endpoint:
        return run_local_llm_via_endpoint(prompt, endpoint, model, api_key, normalized_effort, normalized_api_style)

    raise RuntimeError("ATPROTO_SURVEY_LLM_COMMAND or ATPROTO_SURVEY_LLM_ENDPOINT is required")
