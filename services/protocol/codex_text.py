from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

from fastapi import HTTPException

from services.account_service import account_service
from services.openai_backend_api import OpenAIBackendAPI
from utils.helper import CODEX_TEXT_MODEL


@dataclass
class CodexTextRequest:
    model: str
    instructions: str
    input_items: list[dict[str, Any]]
    reasoning_effort: str = "high"
    account_email: str = ""


def normalize_codex_image_url(value: object) -> str:
    if isinstance(value, dict):
        if value.get("file_id"):
            raise HTTPException(status_code=400, detail={"error": "file_id is not supported for gpt-5.5"})
        value = value.get("url") or value.get("image_url")
    url = str(value or "").strip()
    if not url.startswith(("http://", "https://", "data:image/")):
        raise HTTPException(
            status_code=400,
            detail={"error": "image_url must use http(s) or data:image"},
        )
    return url


def _content_parts(content: object) -> list[object]:
    if isinstance(content, list):
        return list(content)
    if isinstance(content, dict):
        return [content]
    if isinstance(content, str):
        return [content]
    return []


def _instruction_text(content: object) -> list[str]:
    result: list[str] = []
    for part in _content_parts(content):
        if isinstance(part, str):
            text = part.strip()
        elif isinstance(part, dict) and str(part.get("type") or "") in {
            "text",
            "input_text",
            "output_text",
        }:
            text = str(part.get("text") or "").strip()
        else:
            text = ""
        if text:
            result.append(text)
    return result


def _message_content(role: str, content: object) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    text_type = "output_text" if role == "assistant" else "input_text"
    for part in _content_parts(content):
        if isinstance(part, str):
            if part.strip():
                result.append({"type": text_type, "text": part})
            continue
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type") or "").strip()
        if part_type in {"text", "input_text", "output_text"}:
            text = str(part.get("text") or "")
            if text.strip():
                result.append({"type": text_type, "text": text})
            continue
        if part_type in {"image_url", "input_image"}:
            image_value: object = part if part.get("file_id") else part.get("image_url")
            result.append({"type": "input_image", "image_url": normalize_codex_image_url(image_value)})
    return result


def codex_messages(
    messages: list[dict[str, Any]],
    instructions: str = "",
) -> tuple[str, list[dict[str, Any]]]:
    instruction_parts = [str(instructions).strip()] if str(instructions).strip() else []
    input_items: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        content = message.get("content")
        if role in {"system", "developer"}:
            instruction_parts.extend(_instruction_text(content))
            continue
        if role not in {"user", "assistant"}:
            continue
        parts = _message_content(role, content)
        if parts:
            input_items.append({"role": role, "content": parts})
    return "\n\n".join(instruction_parts), input_items


def _completed_output_texts(value: object) -> list[str]:
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_completed_output_texts(item))
        return result
    if not isinstance(value, dict):
        return []
    if value.get("type") == "output_text" and isinstance(value.get("text"), str):
        return [value["text"]]
    result: list[str] = []
    for key in ("output", "content"):
        result.extend(_completed_output_texts(value.get(key)))
    return result


def _unseen_text(emitted: str, candidate: str) -> str:
    if not candidate:
        return ""
    if candidate.startswith(emitted):
        return candidate[len(emitted):]
    if emitted.endswith(candidate):
        return ""
    overlap_limit = min(len(emitted), len(candidate))
    for size in range(overlap_limit, 0, -1):
        if emitted.endswith(candidate[:size]):
            return candidate[size:]
    return candidate


def _codex_text_event_deltas(events: Iterator[dict[str, Any]]) -> Iterator[str]:
    emitted = ""
    completed = False
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        if event_type in {"response.failed", "response.incomplete", "error"}:
            raise RuntimeError(f"Codex text generation failed: {event_type}")
        if event_type == "response.output_text.delta":
            raw_delta = event.get("delta")
            if isinstance(raw_delta, dict):
                raw_delta = raw_delta.get("text")
            delta = str(raw_delta or "")
            if delta:
                emitted += delta
                yield delta
            continue
        elif event_type == "response.output_text.done":
            candidate = str(event.get("text") or event.get("output_text") or "")
        elif event_type == "response.completed":
            response = event.get("response")
            if isinstance(response, dict) and str(response.get("status") or "").lower() in {
                "failed",
                "incomplete",
                "cancelled",
            }:
                raise RuntimeError("Codex text generation failed: response.completed")
            completed = True
            candidate = "".join(_completed_output_texts(response))
        else:
            continue
        delta = _unseen_text(emitted, candidate)
        if delta:
            emitted += delta
            yield delta
    if not completed:
        raise RuntimeError("Codex text response ended without a successful terminal event")
    if not emitted:
        raise RuntimeError("Codex text response completed without final text")


def stream_codex_text_deltas(request: CodexTextRequest) -> Iterator[str]:
    attempted_tokens: set[str] = set()
    last_error: Exception | None = None
    while True:
        try:
            token = account_service.get_text_access_token(
                request.model,
                excluded_tokens=attempted_tokens,
                source_type="codex",
            )
        except Exception:
            if last_error is not None:
                raise last_error
            raise
        if not token or token in attempted_tokens:
            if last_error is not None:
                raise last_error
            raise RuntimeError("no available codex text account")
        attempted_tokens.add(token)
        backend: OpenAIBackendAPI | None = None
        emitted = False
        try:
            backend = OpenAIBackendAPI(access_token=token)
            account = account_service.get_account(token) or {}
            request.account_email = str(account.get("email") or "").strip()
            events = backend.iter_codex_text_response_events(
                instructions=request.instructions,
                input_items=request.input_items,
                model=request.model or CODEX_TEXT_MODEL,
                reasoning_effort=request.reasoning_effort,
            )
            for delta in _codex_text_event_deltas(events):
                emitted = True
                yield delta
            account_service.mark_text_used(token)
            return
        except Exception as exc:
            if emitted:
                raise
            last_error = exc
        finally:
            if backend is not None:
                backend.close()


def collect_codex_text(request: CodexTextRequest) -> str:
    return "".join(stream_codex_text_deltas(request))
