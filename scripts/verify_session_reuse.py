"""验证：同一上游会话内连续生图（复用 conversation_id）是否可行。

流程（控制频率，共 2~3 次上游请求）：
1. 请求 A：不带 conversation_id 生图 → 从 SSE 取 conversation_id
2. 读会话详情，取最后一条 assistant 消息 id 作为续发锚点
3. 请求 B：带 conversation_id + 该锚点作为 parent_message_id 再生图
   → 若 B 的 SSE conversation_id 与 A 相同，说明在同一会话内追加成功
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.openai_backend_api import OpenAIBackendAPI  # noqa: E402
from utils.helper import ensure_ok, new_uuid  # noqa: E402

PROMPT = "生成一张关于沙滩的插画。"
INPUT_PNG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tmp_input.png")


def _load_token() -> str:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "accounts.json")
    data = json.loads(open(path, encoding="utf-8").read())
    items = data if isinstance(data, list) else [data]
    return str(items[0].get("access_token") or "").strip()


def _upload_reference(backend: OpenAIBackendAPI) -> list[dict]:
    with open(INPUT_PNG, "rb") as fh:
        import base64
        b64 = base64.b64encode(fh.read()).decode("ascii")
    return [backend._upload_image(b64, "input.png")]


def _prepare(backend: OpenAIBackendAPI, requirements, model: str, conversation_id: str,
             parent_message_id: str, prompt: str) -> tuple[str, str]:
    path = "/backend-api/f/conversation/prepare"
    parent = parent_message_id or new_uuid()
    payload = {
        "action": "next",
        "conversation_id": conversation_id,
        "parent_message_id": parent,
        "model": model,
        "client_prepare_state": "success",
        "timezone_offset_min": -480,
        "timezone": "Asia/Shanghai",
        "conversation_mode": {"kind": "primary_assistant"},
        "system_hints": ["picture_v2"],
        "partial_query": {
            "id": new_uuid(),
            "author": {"role": "user"},
            "content": {"content_type": "text", "parts": [prompt]},
        },
        "supports_buffering": True,
        "supported_encodings": ["v1"],
        "client_contextual_info": {"app_name": "chatgpt.com"},
    }
    if not conversation_id:
        payload.pop("conversation_id", None)
    response = backend.session.post(
        backend.base_url + path,
        headers=backend._image_headers(path, requirements),
        json=payload,
        timeout=60,
    )
    ensure_ok(response, path)
    return str(response.json().get("conduit_token") or ""), parent


def _start(backend: OpenAIBackendAPI, requirements, conduit_token: str, model: str,
           conversation_id: str, parent_message_id: str, prompt: str, references: list) -> tuple[int, dict]:
    path = "/backend-api/f/conversation"
    parts = [{
        "content_type": "image_asset_pointer",
        "asset_pointer": f"file-service://{item['file_id']}",
        "width": item["width"],
        "height": item["height"],
        "size_bytes": item["file_size"],
    } for item in references]
    parts.append(prompt)
    payload = {
        "action": "next",
        "conversation_id": conversation_id,
        "messages": [{
            "id": new_uuid(),
            "author": {"role": "user"},
            "create_time": time.time(),
            "content": {"content_type": "multimodal_text", "parts": parts} if references else {"content_type": "text", "parts": [prompt]},
            "metadata": {"serialization_metadata": {"custom_symbol_offsets": []}},
        }],
        "parent_message_id": parent_message_id,
        "model": model,
        "client_prepare_state": "sent",
        "timezone_offset_min": -480,
        "timezone": "Asia/Shanghai",
        "conversation_mode": {"kind": "primary_assistant"},
        "enable_message_followups": True,
        "system_hints": ["picture_v2"],
        "supports_buffering": True,
        "supported_encodings": ["v1"],
        "client_contextual_info": {"app_name": "chatgpt.com"},
        "paragen_cot_summary_display_override": "allow",
        "force_parallel_switch": "auto",
        "thinking_effort": "extended",
    }
    if not conversation_id:
        payload.pop("conversation_id", None)
    response = backend.session.post(
        backend.base_url + path,
        headers=backend._image_headers(path, requirements, conduit_token, "text/event-stream"),
        json=payload,
        timeout=300,
        stream=True,
    )
    ensure_ok(response, path)
    # 读 SSE 头部，抓 conversation_id 与事件类型
    found_conv = ""
    types: dict[str, int] = {}
    errors: list[str] = []
    try:
        for line in response.iter_lines():
            if not line:
                continue
            text = line.decode("utf-8", "replace")
            if not text.startswith("data:"):
                continue
            data = text[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            etype = str(event.get("type") or "<none>")
            types[etype] = types.get(etype, 0) + 1
            found_conv = found_conv or str(event.get("conversation_id") or "")
            if etype == "error":
                errors.append(json.dumps(event, ensure_ascii=False)[:300])
            if sum(types.values()) >= 15:
                break
    except Exception as exc:
        errors.append(repr(exc)[:200])
    finally:
        response.close()
    return response.status_code, {"conversation_id": found_conv, "types": types, "errors": errors}


def _latest_assistant_message_id(backend: OpenAIBackendAPI, conversation_id: str) -> str:
    data = backend._get_conversation(conversation_id)
    best: list[tuple[float, str]] = []
    for mid, node in (data.get("mapping") or {}).items():
        msg = (node or {}).get("message") or {}
        if str((msg.get("author") or {}).get("role") or "") == "assistant":
            best.append((float(msg.get("create_time") or 0.0), str(mid)))
    best.sort()
    return best[-1][1] if best else ""


def main() -> None:
    token = _load_token()
    backend = OpenAIBackendAPI(access_token=token)
    try:
        print("[1] bootstrap + requirements")
        backend._bootstrap()
        requirements = backend._get_chat_requirements()
        model = backend._image_model_slug("gpt-image-2")
        references = _upload_reference(backend)
        print(f"    模型 slug: {model}")

        print("\n[2] 请求 A：首次生图（新会话）")
        conduit, parent = _prepare(backend, requirements, model, "", "", PROMPT)
        status, info = _start(backend, requirements, conduit, model, "", parent, PROMPT, references)
        conv_a = info["conversation_id"]
        print(f"    HTTP {status} | conversation_id={conv_a} | events={info['types']}")
        if info["errors"]:
            print("    errors:", info["errors"][:2])
        if not conv_a:
            raise SystemExit("未从 SSE 拿到 conversation_id")

        print("\n[2b] 等待请求 A 的图片真正生成完成（轮询会话）...")
        polled_file_ids, polled_sediment_ids = backend._poll_image_results(
            conv_a, timeout_secs=180.0, initial_file_ids=[], initial_sediment_ids=[],
        )
        print(f"    A 完成：file_ids={polled_file_ids} sediment_ids={polled_sediment_ids}")
        # 图片生成可能改变会话结构，重新获取 requirements（旧 token 可能过期）
        requirements = backend._get_chat_requirements()
        time.sleep(3)

        print("\n[3] 读会话详情，找续发锚点（最后 assistant 消息 id）")
        anchor = _latest_assistant_message_id(backend, conv_a)
        print(f"    anchor message id: {anchor}")
        if not anchor:
            print("    [WARN] 未找到 assistant 消息，尝试用 parent 继续")

        print("\n[4] 请求 B：带 conversation_id 续发生图（应追加到同一会话）")
        conduit2, parent2 = _prepare(backend, requirements, model, conv_a, anchor or parent, "修改上一张图：加一只海鸥")
        status2, info2 = _start(backend, requirements, conduit2, model, conv_a, parent2,
                                "修改上一张图：加一只海鸥", references)
        conv_b = info2["conversation_id"]
        print(f"    HTTP {status2} | conversation_id={conv_b} | events={info2['types']}")
        if info2["errors"]:
            print("    errors:", info2["errors"][:2])

        print("\n[5] 结论")
        same = bool(conv_b) and conv_b == conv_a
        print(f"    B 的 conversation_id == A 的 conversation_id: {same}")
        print(f"    事件类型: {info2['types']}")
        if same and not info2["errors"]:
            print("    >>> 同会话续发生图可行")
        else:
            print("    >>> 需要进一步分析（可能需重试/不同锚点）")
    finally:
        backend.close()


if __name__ == "__main__":
    main()
