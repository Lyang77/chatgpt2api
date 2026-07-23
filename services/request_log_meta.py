from __future__ import annotations

import math
from collections import Counter
from typing import Any


def _safe_string(value: object, *, max_length: int = 200) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if text and len(text) <= max_length else None


def _safe_number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _safe_count(value: object) -> int | None:
    number = _safe_number(value)
    if not isinstance(number, int) or number < 0:
        return None
    return number


def _text_chars(value: object) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(_text_chars(item) for item in value)
    if not isinstance(value, dict):
        return 0
    return sum(_text_chars(value.get(key)) for key in ("text", "input_text", "content") if key in value)


def _role_counts(value: object) -> dict[str, int]:
    if not isinstance(value, list):
        return {}
    counts: Counter[str] = Counter()
    for item in value:
        if not isinstance(item, dict):
            continue
        role = _safe_string(item.get("role"), max_length=32)
        if role:
            counts[role] += 1
    return dict(counts)


def _image_input_count(value: object) -> int:
    if isinstance(value, list):
        return sum(_image_input_count(item) for item in value)
    if not isinstance(value, dict):
        return 0
    item_type = _safe_string(value.get("type"), max_length=64)
    if item_type in {"image", "image_url", "input_image"}:
        return 1
    if any(key in value for key in ("image", "image_url", "input_image")):
        return 1
    return sum(_image_input_count(item) for item in value.values())


def _object_type(value: object) -> str | None:
    if isinstance(value, str):
        return _safe_string(value, max_length=64)
    if isinstance(value, dict):
        return _safe_string(value.get("type"), max_length=64)
    return None


def build_image_request_meta(
    payload: dict[str, Any],
    *,
    mode: str,
    reference_image_count: int | None = None,
    mask_image_count: int | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "mode": "edit" if mode == "edit" else "generate",
        "quality": _safe_string(payload.get("quality")) or "auto",
        "n": _safe_count(payload.get("n")) or 1,
        "output_format": _safe_string(payload.get("output_format")) or "png",
        "response_format": _safe_string(payload.get("response_format")) or "b64_json",
    }
    for key in ("size", "client_task_id"):
        value = _safe_string(payload.get(key))
        if value is not None:
            meta[key] = value
    if isinstance(payload.get("stream"), bool):
        meta["stream"] = payload["stream"]

    references = reference_image_count
    if references is None and isinstance(payload.get("images"), list):
        references = len(payload["images"])
    masks = mask_image_count
    if masks is None and isinstance(payload.get("mask"), list):
        masks = len(payload["mask"])
    if isinstance(references, int) and references >= 0:
        meta["reference_image_count"] = references
    if isinstance(masks, int) and masks >= 0:
        meta["mask_image_count"] = masks
    return meta


def build_text_request_meta(payload: dict[str, Any], *, protocol: str) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    for key in ("stream", "store"):
        if isinstance(payload.get(key), bool):
            meta[key] = payload[key]
    for key in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
        value = _safe_count(payload.get(key))
        if value is not None:
            meta[key] = value
    for key in ("temperature", "top_p"):
        value = _safe_number(payload.get(key))
        if value is not None:
            meta[key] = value

    modalities = payload.get("modalities")
    if isinstance(modalities, list):
        safe_modalities = [
            item for item in (_safe_string(value, max_length=32) for value in modalities[:8]) if item
        ]
        if safe_modalities:
            meta["modalities"] = safe_modalities

    tool_choice_type = _object_type(payload.get("tool_choice"))
    if tool_choice_type:
        meta["tool_choice_type"] = tool_choice_type
    response_format_type = _object_type(payload.get("response_format"))
    if response_format_type:
        meta["response_format_type"] = response_format_type
    reasoning = payload.get("reasoning")
    reasoning_effort = _safe_string(payload.get("reasoning_effort"), max_length=32)
    if reasoning_effort is None and isinstance(reasoning, dict):
        reasoning_effort = _safe_string(reasoning.get("effort"), max_length=32)
    if reasoning_effort:
        meta["reasoning_effort"] = reasoning_effort

    tools = payload.get("tools")
    if isinstance(tools, list):
        meta["tool_count"] = len(tools)

    if protocol in {"chat_completions", "messages"}:
        messages = payload.get("messages")
        if isinstance(messages, list):
            meta["message_count"] = len(messages)
            roles = _role_counts(messages)
            if roles:
                meta["role_counts"] = roles
            image_count = _image_input_count(messages)
            if image_count:
                meta["image_input_count"] = image_count
    if protocol == "responses":
        input_value = payload.get("input")
        if isinstance(input_value, list):
            meta["input_item_count"] = len(input_value)
            roles = _role_counts(input_value)
            if roles:
                meta["role_counts"] = roles
        elif isinstance(input_value, str) and input_value:
            meta["input_item_count"] = 1
        image_count = _image_input_count(input_value)
        if image_count:
            meta["image_input_count"] = image_count
        input_chars = _text_chars(input_value)
        if input_chars:
            meta["input_chars"] = input_chars

    prompt = payload.get("prompt")
    if isinstance(prompt, str) and prompt:
        meta["prompt_chars"] = len(prompt)
    system_value = payload.get("instructions") if protocol == "responses" else payload.get("system")
    system_chars = _text_chars(system_value)
    if system_chars:
        meta["system_chars"] = system_chars

    if protocol == "editable_file":
        client_task_id = _safe_string(payload.get("client_task_id"))
        if client_task_id:
            meta["client_task_id"] = client_task_id
        references = payload.get("base64_images")
        if isinstance(references, list):
            meta["reference_image_count"] = len(references)
    return meta
