"""验证脚本：测试上游 /backend-api/f/conversation 是否接受
1) 自定义 model slug（如 gpt-5-5 / gpt-5-6）
2) thinking_effort 字段（low/medium/high/extended）

不改动项目代码，在脚本内复刻 prepare/start 请求逻辑注入测试参数。

用法（在 chatgpt2api 根目录）：
    python scripts/verify_image_edits_upstream.py            # 只验证上游接受度（发 start，读 SSE 头部，不取图）
    python scripts/verify_image_edits_upstream.py --full     # 完整出图验证（会消耗额度）

前置条件：data/accounts.json 里至少有一个有效 ChatGPT access_token（有画图权限）。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.openai_backend_api import (  # noqa: E402
    OpenAIBackendAPI,
    ChatRequirements,
)
from utils.helper import ensure_ok, new_uuid  # noqa: E402

# (label, model_slug, thinking_effort) —— thinking_effort 只注入 start 阶段（prepare 不接受该字段）
VARIANTS = [
    ("5.6sol-thinking 极高模型", "gpt-5.6-sol-thinking", ""),
]

PROMPT = "把这张图稍微调亮一点，保持内容不变。"


def _load_first_token() -> str:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "accounts.json")
    if not os.path.exists(path):
        raise SystemExit(f"accounts.json 不存在: {path}")
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    data = json.loads(raw) if raw.strip() else []
    if not isinstance(data, list) or not data:
        raise SystemExit("accounts.json 为空，请先配置账号")
    token = str(data[0].get("access_token") or "").strip()
    if not token:
        raise SystemExit("第一个账号缺少 access_token")
    return token


def _load_input_image(explicit_path: str = "") -> bytes:
    if explicit_path:
        with open(explicit_path, "rb") as fh:
            data = fh.read()
        print(f"[input] 使用指定输入图: {explicit_path} ({len(data)} bytes)")
        return data
    images_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "images")
    candidates = []
    for root, _dirs, files in os.walk(images_dir):
        for name in files:
            if name.lower().endswith(".png"):
                full = os.path.join(root, name)
                size = os.path.getsize(full)
                if 512 <= size <= 8 * 1024 * 1024:
                    candidates.append((size, full))
    if candidates:
        candidates.sort()
        path = candidates[-1][1]
        with open(path, "rb") as fh:
            data = fh.read()
        print(f"[input] 使用输入图: {path} ({len(data)} bytes)")
        return data
    # 没有现成图时用 PIL 生成一张测试图
    from io import BytesIO
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1024, 1024), (40, 90, 160))
    draw = ImageDraw.Draw(img)
    for i in range(0, 1024, 16):
        draw.rectangle([i, 0, i + 8, 1024], fill=(i % 255, 120, 200))
    buf = BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()
    print(f"[input] 自动生成测试输入图 ({len(data)} bytes)")
    return data


def _prepare_variant(backend: OpenAIBackendAPI, requirements: ChatRequirements,
                     model_slug: str, thinking: str) -> str:
    """复刻 _prepare_image_conversation，支持注入 model slug。
    注意：prepare 阶段不注入 thinking_effort（上游 422 拒绝该字段）。"""
    path = "/backend-api/f/conversation/prepare"
    payload = {
        "action": "next",
        "fork_from_shared_post": False,
        "parent_message_id": new_uuid(),
        "model": model_slug,
        "client_prepare_state": "success",
        "timezone_offset_min": -480,
        "timezone": "Asia/Shanghai",
        "conversation_mode": {"kind": "primary_assistant"},
        "system_hints": ["picture_v2"],
        "partial_query": {
            "id": new_uuid(),
            "author": {"role": "user"},
            "content": {"content_type": "text", "parts": [PROMPT]},
        },
        "supports_buffering": True,
        "supported_encodings": ["v1"],
        "client_contextual_info": {"app_name": "chatgpt.com"},
    }
    response = backend.session.post(
        backend.base_url + path,
        headers=backend._image_headers(path, requirements),
        json=payload,
        timeout=60,
    )
    ensure_ok(response, path)
    return str(response.json().get("conduit_token") or "")


def _start_variant(backend: OpenAIBackendAPI, requirements: ChatRequirements, conduit_token: str,
                   model_slug: str, thinking: str, references: list) -> object:
    """复刻 _start_image_generation，支持注入 model slug / thinking_effort。"""
    parts = [{
        "content_type": "image_asset_pointer",
        "asset_pointer": f"file-service://{item['file_id']}",
        "width": item["width"],
        "height": item["height"],
        "size_bytes": item["file_size"],
    } for item in references]
    parts.append(PROMPT)
    content = {"content_type": "multimodal_text", "parts": parts}
    metadata = {
        "developer_mode_connector_ids": [],
        "selected_github_repos": [],
        "selected_all_github_repos": False,
        "system_hints": ["picture_v2"],
        "serialization_metadata": {"custom_symbol_offsets": []},
        "attachments": [{
            "id": item["file_id"],
            "mimeType": item["mime_type"],
            "name": item["file_name"],
            "size": item["file_size"],
            "width": item["width"],
            "height": item["height"],
        } for item in references],
    }
    payload = {
        "action": "next",
        "messages": [{
            "id": new_uuid(),
            "author": {"role": "user"},
            "create_time": time.time(),
            "content": content,
            "metadata": metadata,
        }],
        "parent_message_id": new_uuid(),
        "model": model_slug,
        "client_prepare_state": "sent",
        "timezone_offset_min": -480,
        "timezone": "Asia/Shanghai",
        "conversation_mode": {"kind": "primary_assistant"},
        "enable_message_followups": True,
        "system_hints": ["picture_v2"],
        "supports_buffering": True,
        "supported_encodings": ["v1"],
        "client_contextual_info": {
            "is_dark_mode": False,
            "time_since_loaded": 1200,
            "page_height": 1072,
            "page_width": 1724,
            "pixel_ratio": 1.2,
            "screen_height": 1440,
            "screen_width": 2560,
            "app_name": "chatgpt.com",
        },
        "paragen_cot_summary_display_override": "allow",
        "force_parallel_switch": "auto",
    }
    if thinking:
        payload["thinking_effort"] = thinking
    path = "/backend-api/f/conversation"
    response = backend.session.post(
        backend.base_url + path,
        headers=backend._image_headers(path, requirements, conduit_token, "text/event-stream"),
        json=payload,
        timeout=300,
        stream=True,
    )
    ensure_ok(response, path)
    return response


def _read_sse_head(response, max_events: int = 20) -> dict:
    """读 SSE 前几个事件，返回事件类型统计和错误信息。"""
    types: dict[str, int] = {}
    errors: list[str] = []
    file_ids: list[str] = []
    sediment_ids: list[str] = []
    count = 0
    try:
        for line in response.iter_lines():
            if not line:
                continue
            text = line.decode("utf-8", "replace")
            if not text.startswith("data:"):
                continue
            payload = text[5:].strip()
            if not payload:
                continue
            if payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            etype = str(event.get("type") or "<none>")
            types[etype] = types.get(etype, 0) + 1
            if etype == "error":
                errors.append(json.dumps(event, ensure_ascii=False)[:500])
            if event.get("conversation_id"):
                pass
            count += 1
            if count >= max_events:
                break
    except Exception as exc:
        errors.append(f"read_exception: {repr(exc)[:300]}")
    return {"types": types, "errors": errors, "events_read": count}


def _run_variant(backend: OpenAIBackendAPI, image_b64: str, label: str,
                 model_slug: str, thinking: str, full: bool) -> dict:
    print(f"\n=== 变体: {label} | slug={model_slug or '(默认映射)'} | thinking={thinking or '(无)'} ===")
    refs = [backend._upload_image(image_b64, "input.png")]
    backend._bootstrap()
    requirements = backend._get_chat_requirements()
    conduit = _prepare_variant(backend, requirements, model_slug, thinking)
    print(f"[prepare] conduit_token 获取成功: {'yes' if conduit else 'NO (空!)'}")
    resp = _start_variant(backend, requirements, conduit, model_slug, thinking, refs)
    print(f"[start] HTTP {resp.status_code}")
    if resp.status_code != 200:
        body = resp.text[:800]
        print(f"[start] 非 200 响应体: {body}")
        resp.close()
        return {"label": label, "accepted": False, "http": resp.status_code, "detail": body[:300]}
    head = _read_sse_head(resp)
    print(f"[sse] 事件: {head['types']} | 读取 {head['events_read']} 个")
    if head["errors"]:
        print(f"[sse] 错误: {head['errors'][:3]}")
    resp.close()
    accepted = not head["errors"] and bool(head["types"])
    return {"label": label, "accepted": accepted, "http": 200, "events": head["types"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="完整出图（消耗额度）")
    parser.add_argument("--model", default="", help="只跑指定 model slug")
    parser.add_argument("--image", default="", help="指定输入图路径")
    args = parser.parse_args()

    token = _load_first_token()
    image_data = _load_input_image(args.image)
    image_b64 = base64.b64encode(image_data).decode("ascii")

    backend = OpenAIBackendAPI(access_token=token)
    try:
        me = backend._get_me()
        print("[me] token 有效:", {k: me.get(k) for k in ("email", "chatgpt_plan_type", "account_plan_type") if me.get(k)})

        print("\n== 登录态模型列表 ==")
        try:
            models = backend.list_models()
            ids = [str(m.get("id")) for m in (models.get("data") or [])]
            print("count:", len(ids))
            print("  ", ", ".join(ids))
        except Exception as exc:
            print("list_models failed:", repr(exc)[:300])

        results = []
        for label, slug, thinking in VARIANTS:
            if args.model and slug != args.model:
                continue
            try:
                results.append(_run_variant(backend, image_b64, label, slug, thinking, args.full))
            except Exception as exc:
                print(f"[变体失败] {label}: {repr(exc)[:400]}")
                results.append({"label": label, "accepted": False, "error": repr(exc)[:200]})

        print("\n========== 汇总 ==========")
        for r in results:
            print(f"{r['label']}: accepted={r.get('accepted')} http={r.get('http')} "
                  f"events={r.get('events')} detail={r.get('detail') or r.get('error') or ''}")
    finally:
        backend.close()


if __name__ == "__main__":
    main()
