from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, TypeVar

from fastapi import HTTPException

from services.account_service import account_service
from services.openai_backend_api import OpenAIBackendAPI
from utils.helper import CODEX_TEXT_DEFAULT_REASONING_EFFORT, CODEX_TEXT_MODEL


class CodexTextGenerationError(RuntimeError):
    def __init__(self, event_type: str, diagnostic_detail: dict[str, object]) -> None:
        super().__init__(f"Codex text generation failed: {event_type}")
        self.diagnostic_detail = diagnostic_detail


@dataclass
class CodexTextRequest:
    model: str
    instructions: str
    input_items: list[dict[str, Any]]
    reasoning_effort: str = CODEX_TEXT_DEFAULT_REASONING_EFFORT
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_choice: object | None = None
    parallel_tool_calls: bool | None = None
    account_email: str = ""


T = TypeVar("T")


def normalize_codex_function_tools(tools: object, model: str) -> list[dict[str, Any]]:
    if tools is None or tools == "":
        return []
    if not isinstance(tools, list):
        raise HTTPException(status_code=400, detail={"error": "tools must be an array"})
    normalized: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict) or str(tool.get("type") or "").strip() != "function":
            raise HTTPException(
                status_code=400,
                detail={"error": f"{model} supports function tools only"},
            )
        function = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = str(function.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail={"error": "function tool name is required"})
        parameters = function.get("parameters")
        if parameters is None or parameters == {}:
            parameters = {"type": "object", "properties": {}}
        if not isinstance(parameters, dict):
            raise HTTPException(
                status_code=400,
                detail={"error": f"parameters for function tool {name} must be an object"},
            )
        item: dict[str, Any] = {
            "type": "function",
            "name": name,
            "parameters": parameters,
        }
        description = function.get("description")
        if isinstance(description, str) and description:
            item["description"] = description
        if "strict" in function:
            if not isinstance(function.get("strict"), bool):
                raise HTTPException(
                    status_code=400,
                    detail={"error": f"strict for function tool {name} must be a boolean"},
                )
            item["strict"] = function["strict"]
        normalized.append(item)
    return normalized


def normalize_codex_tool_choice(
    value: object,
    tools: list[dict[str, Any]],
) -> object | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        choice = value.strip().lower()
        if choice not in {"none", "auto", "required"}:
            raise HTTPException(status_code=400, detail={"error": f"unsupported tool_choice: {value}"})
        if not tools:
            if choice == "none":
                return None
            raise HTTPException(status_code=400, detail={"error": "tool_choice requires function tools"})
        return choice
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail={"error": "tool_choice must be a string or object"})
    function = value.get("function") if isinstance(value.get("function"), dict) else value
    if str(value.get("type") or "").strip() != "function":
        raise HTTPException(status_code=400, detail={"error": "only function tool_choice is supported"})
    name = str(function.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail={"error": "function tool_choice name is required"})
    if name not in {str(tool.get("name") or "") for tool in tools}:
        raise HTTPException(status_code=400, detail={"error": f"unknown function tool_choice: {name}"})
    return {"type": "function", "name": name}


def codex_tool_config(
    body: dict[str, Any],
    model: str,
) -> tuple[list[dict[str, Any]], object | None, bool | None]:
    tools = normalize_codex_function_tools(body.get("tools"), model)
    tool_choice = normalize_codex_tool_choice(body.get("tool_choice"), tools)
    parallel_value = body.get("parallel_tool_calls")
    if parallel_value is not None and not isinstance(parallel_value, bool):
        raise HTTPException(status_code=400, detail={"error": "parallel_tool_calls must be a boolean"})
    parallel_tool_calls = parallel_value if tools else None
    return tools, tool_choice, parallel_tool_calls


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


def _json_string(value: object) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _function_call_item(value: dict[str, Any]) -> dict[str, Any]:
    function = value.get("function") if isinstance(value.get("function"), dict) else value
    call_id = str(value.get("call_id") or value.get("tool_call_id") or value.get("id") or "").strip()
    name = str(function.get("name") or value.get("name") or "").strip()
    if not call_id or not name:
        raise HTTPException(
            status_code=400,
            detail={"error": "function call requires call_id and name"},
        )
    item: dict[str, Any] = {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": _json_string(function.get("arguments")),
    }
    item_id = str(value.get("id") or "").strip()
    if item_id and item_id != call_id:
        item["id"] = item_id
    return item


def _function_call_output_item(value: dict[str, Any]) -> dict[str, Any]:
    call_id = str(value.get("call_id") or value.get("tool_call_id") or "").strip()
    if not call_id:
        raise HTTPException(status_code=400, detail={"error": "function call output requires call_id"})
    item: dict[str, Any] = {
        "type": "function_call_output",
        "call_id": call_id,
        "output": _json_string(value.get("output", value.get("content"))),
    }
    item_id = str(value.get("id") or "").strip()
    if item_id:
        item["id"] = item_id
    return item


def _reasoning_item(value: dict[str, Any]) -> dict[str, Any]:
    item: dict[str, Any] = {"type": "reasoning"}
    for key in ("id", "encrypted_content", "summary", "content", "status"):
        if key in value:
            item[key] = value[key]
    return item


def codex_messages(
    messages: list[dict[str, Any]],
    instructions: str = "",
) -> tuple[str, list[dict[str, Any]]]:
    instruction_parts = [str(instructions).strip()] if str(instructions).strip() else []
    input_items: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        item_type = str(message.get("type") or "").strip()
        if item_type == "function_call":
            input_items.append(_function_call_item(message))
            continue
        if item_type == "function_call_output":
            input_items.append(_function_call_output_item(message))
            continue
        if item_type == "reasoning":
            input_items.append(_reasoning_item(message))
            continue
        role = str(message.get("role") or "").strip().lower()
        content = message.get("content")
        if role in {"system", "developer"}:
            instruction_parts.extend(_instruction_text(content))
            continue
        if role == "tool":
            input_items.append(_function_call_output_item(message))
            continue
        if role not in {"user", "assistant"}:
            continue
        parts = _message_content(role, content)
        if parts:
            input_items.append({"role": role, "content": parts})
        if role == "assistant":
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                for tool_call in tool_calls:
                    if isinstance(tool_call, dict):
                        input_items.append(_function_call_item(tool_call))
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


def _clean_codex_diagnostic(candidates: dict[str, object]) -> dict[str, object]:
    limits = {
        "event_type": 200,
        "type": 200,
        "code": 200,
        "message": 1000,
        "response_id": 200,
        "incomplete_reason": 200,
    }
    result: dict[str, object] = {}
    for key, limit in limits.items():
        value = candidates.get(key)
        if not isinstance(value, (str, int, float, bool)):
            continue
        cleaned = OpenAIBackendAPI._codex_log_value(value, limit)
        if cleaned in {None, ""}:
            continue
        result[key] = cleaned
    return result


def _codex_failure_diagnostic(event: dict[str, Any]) -> dict[str, object]:
    event_type = str(event.get("type") or "")
    raw_response = event.get("response")
    response = raw_response if isinstance(raw_response, dict) else {}
    raw_error = event.get("error")
    if not isinstance(raw_error, dict):
        raw_error = response.get("error")
    error = raw_error if isinstance(raw_error, dict) else {}
    raw_incomplete = event.get("incomplete_details")
    if not isinstance(raw_incomplete, dict):
        raw_incomplete = response.get("incomplete_details")
    incomplete = raw_incomplete if isinstance(raw_incomplete, dict) else {}
    return _clean_codex_diagnostic({
        "event_type": event_type,
        "type": error.get("type"),
        "code": error.get("code"),
        "message": error.get("message"),
        "response_id": event.get("response_id") or response.get("id") or event.get("id"),
        "incomplete_reason": incomplete.get("reason"),
    })


def _codex_text_event_deltas(events: Iterator[dict[str, Any]]) -> Iterator[str]:
    emitted = ""
    completed = False
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        if event_type in {"response.failed", "response.incomplete", "error"}:
            raise CodexTextGenerationError(event_type, _codex_failure_diagnostic(event))
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
                raise CodexTextGenerationError(event_type, _codex_failure_diagnostic(event))
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


def _codex_tool_event_deltas(events: Iterator[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    emitted_text = ""
    completed = False
    call_states: dict[str, dict[str, Any]] = {}
    reasoning_states: dict[str, dict[str, Any]] = {}

    def reasoning_state_for(
        event: dict[str, Any],
        item: dict[str, Any],
    ) -> dict[str, Any]:
        item_id = str(item.get("id") or event.get("item_id") or "").strip()
        output_index = event.get("output_index", len(reasoning_states))
        key = item_id or f"output:{output_index}"
        state = reasoning_states.get(key)
        if state is None:
            state = {
                "index": len(reasoning_states),
                "output_index": output_index,
                "item": {"type": "reasoning", "summary": []},
                "started": False,
                "done": False,
            }
            reasoning_states[key] = state
        state["output_index"] = output_index
        state["item"].update(_reasoning_item(item))
        return state

    def start_reasoning(state: dict[str, Any], output: list[dict[str, Any]]) -> None:
        if state["started"]:
            return
        state["started"] = True
        output.append({
            "type": "reasoning_start",
            "index": state["index"],
            "output_index": state["output_index"],
            "item": dict(state["item"]),
        })

    def finish_reasoning(state: dict[str, Any], output: list[dict[str, Any]]) -> None:
        start_reasoning(state, output)
        if state["done"]:
            return
        state["done"] = True
        if state["item"].get("status") == "in_progress":
            state["item"]["status"] = "completed"
        output.append({
            "type": "reasoning_done",
            "index": state["index"],
            "output_index": state["output_index"],
            "item": dict(state["item"]),
        })

    def state_for(event: dict[str, Any], item: dict[str, Any] | None = None) -> dict[str, Any]:
        item = item or {}
        item_id = str(event.get("item_id") or item.get("id") or "").strip()
        call_id = str(event.get("call_id") or item.get("call_id") or item_id).strip()
        key = item_id or call_id
        if not key:
            raise RuntimeError("Codex function call is missing item_id and call_id")
        state = call_states.get(key)
        if state is None:
            state = {
                "item_id": item_id or key,
                "call_id": call_id or key,
                "name": "",
                "arguments": "",
                "index": len(call_states),
                "output_index": event.get("output_index", len(call_states)),
                "started": False,
                "done": False,
            }
            call_states[key] = state
        state["name"] = str(event.get("name") or item.get("name") or state["name"]).strip()
        state["call_id"] = str(event.get("call_id") or item.get("call_id") or state["call_id"]).strip()
        if "output_index" in event:
            state["output_index"] = event["output_index"]
        return state

    def start_call(state: dict[str, Any], output: list[dict[str, Any]]) -> None:
        if state["started"]:
            return
        if not state["name"]:
            return
        state["started"] = True
        output.append({
            "type": "tool_call_start",
            "index": state["index"],
            "output_index": state["output_index"],
            "item_id": state["item_id"],
            "call_id": state["call_id"],
            "name": state["name"],
        })
        if state["arguments"]:
            output.append({
                "type": "tool_call_arguments_delta",
                "index": state["index"],
                "output_index": state["output_index"],
                "item_id": state["item_id"],
                "call_id": state["call_id"],
                "delta": state["arguments"],
            })

    def add_arguments(
        state: dict[str, Any],
        value: object,
        output: list[dict[str, Any]],
        *,
        is_delta: bool,
    ) -> None:
        candidate = str(value or "")
        delta = candidate if is_delta else _unseen_text(str(state["arguments"]), candidate)
        if not delta:
            return
        state["arguments"] += delta
        if state["started"]:
            output.append({
                "type": "tool_call_arguments_delta",
                "index": state["index"],
                "output_index": state["output_index"],
                "item_id": state["item_id"],
                "call_id": state["call_id"],
                "delta": delta,
            })

    def finish_call(state: dict[str, Any], output: list[dict[str, Any]]) -> None:
        start_call(state, output)
        if not state["started"]:
            raise RuntimeError("Codex function call is missing a name")
        if state["done"]:
            return
        state["done"] = True
        output.append({
            "type": "tool_call_done",
            "index": state["index"],
            "output_index": state["output_index"],
            "item_id": state["item_id"],
            "call_id": state["call_id"],
            "name": state["name"],
            "arguments": state["arguments"],
        })

    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        if event_type in {"response.failed", "response.incomplete", "error"}:
            raise CodexTextGenerationError(event_type, _codex_failure_diagnostic(event))
        output: list[dict[str, Any]] = []
        if event_type == "response.output_text.delta":
            raw_delta = event.get("delta")
            if isinstance(raw_delta, dict):
                raw_delta = raw_delta.get("text")
            delta = str(raw_delta or "")
            if delta:
                emitted_text += delta
                output.append({
                    "type": "text_delta",
                    "delta": delta,
                    "item_id": str(event.get("item_id") or ""),
                    "output_index": event.get("output_index", 0),
                })
        elif event_type == "response.output_text.done":
            candidate = str(event.get("text") or event.get("output_text") or "")
            delta = _unseen_text(emitted_text, candidate)
            if delta:
                emitted_text += delta
                output.append({
                    "type": "text_delta",
                    "delta": delta,
                    "item_id": str(event.get("item_id") or ""),
                    "output_index": event.get("output_index", 0),
                })
        elif event_type == "response.output_item.added":
            item = event.get("item") if isinstance(event.get("item"), dict) else {}
            if item.get("type") == "function_call":
                state = state_for(event, item)
                start_call(state, output)
                add_arguments(state, item.get("arguments"), output, is_delta=False)
            elif item.get("type") == "reasoning":
                start_reasoning(reasoning_state_for(event, item), output)
        elif event_type == "response.function_call_arguments.delta":
            state = state_for(event)
            start_call(state, output)
            add_arguments(state, event.get("delta"), output, is_delta=True)
        elif event_type == "response.function_call_arguments.done":
            state = state_for(event)
            start_call(state, output)
            add_arguments(state, event.get("arguments"), output, is_delta=False)
            finish_call(state, output)
        elif event_type == "response.output_item.done":
            item = event.get("item") if isinstance(event.get("item"), dict) else {}
            if item.get("type") == "function_call":
                state = state_for(event, item)
                start_call(state, output)
                add_arguments(state, item.get("arguments"), output, is_delta=False)
                finish_call(state, output)
            elif item.get("type") == "reasoning":
                finish_reasoning(reasoning_state_for(event, item), output)
        elif event_type == "response.completed":
            response = event.get("response") if isinstance(event.get("response"), dict) else {}
            if str(response.get("status") or "").lower() in {"failed", "incomplete", "cancelled"}:
                raise CodexTextGenerationError(event_type, _codex_failure_diagnostic(event))
            for output_index, item in enumerate(response.get("output") or []):
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "reasoning":
                    finish_reasoning(
                        reasoning_state_for({"output_index": output_index}, item),
                        output,
                    )
                elif item.get("type") == "function_call":
                    state = state_for({"output_index": output_index}, item)
                    start_call(state, output)
                    add_arguments(state, item.get("arguments"), output, is_delta=False)
                    finish_call(state, output)
            for state in call_states.values():
                finish_call(state, output)
            candidate = "".join(_completed_output_texts(response))
            delta = _unseen_text(emitted_text, candidate)
            if delta:
                emitted_text += delta
                output.append({"type": "text_delta", "delta": delta, "item_id": "", "output_index": 0})
            if not emitted_text and not call_states:
                raise RuntimeError("Codex tool response completed without text or function calls")
            completed = True
            output.append({"type": "completed"})
        for value in output:
            yield value

    if not completed:
        raise RuntimeError("Codex tool response ended without a successful terminal event")


def _stream_codex_values(
    request: CodexTextRequest,
    parser: Callable[[Iterator[dict[str, Any]]], Iterator[T]],
) -> Iterator[T]:
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
            transport_args: dict[str, Any] = {
                "instructions": request.instructions,
                "input_items": request.input_items,
                "model": request.model or CODEX_TEXT_MODEL,
                "reasoning_effort": request.reasoning_effort,
            }
            if request.tools:
                transport_args["tools"] = request.tools
            if request.tool_choice is not None:
                transport_args["tool_choice"] = request.tool_choice
            if request.parallel_tool_calls is not None:
                transport_args["parallel_tool_calls"] = request.parallel_tool_calls
            events = backend.iter_codex_text_response_events(
                **transport_args,
            )
            for delta in parser(events):
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


def stream_codex_text_deltas(request: CodexTextRequest) -> Iterator[str]:
    yield from _stream_codex_values(request, _codex_text_event_deltas)


def stream_codex_tool_events(request: CodexTextRequest) -> Iterator[dict[str, Any]]:
    yield from _stream_codex_values(request, _codex_tool_event_deltas)


def collect_codex_text(request: CodexTextRequest) -> str:
    return "".join(stream_codex_text_deltas(request))
