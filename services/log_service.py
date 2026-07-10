from __future__ import annotations

import base64
import binascii
import itertools
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse

from services.config import DATA_DIR
from services.image_storage_service import image_storage_service
from services.log_store import SQLiteLogStore
from services.protocol.error_response import anthropic_error_response, openai_error_response
from utils.helper import anthropic_sse_stream, sse_json_stream

LOG_TYPE_CALL = "call"
LOG_TYPE_ACCOUNT = "account"
INTERNAL_RESPONSE_KEYS = {"_account_email", "_conversation_id", "_cache_hit"}
MAX_RESPONSE_TEXT_CHARS = 12000
LOG_LIST_OMITTED_DETAIL_KEYS = {"request_text", "response_text", "request_urls"}
MAX_REQUEST_IMAGE_URLS = 12
REQUEST_IMAGE_URL_KEYS = {
    "image",
    "image[]",
    "images",
    "images[]",
    "image_url",
    "image_url[]",
    "image_urls",
    "imageurl",
    "imageurls",
    "input_image_url",
    "input_image_urls",
    "inputimageurl",
    "inputimageurls",
    "reference_image",
    "reference_images",
    "reference_image_url",
    "reference_image_urls",
    "referenceimageurl",
    "referenceimageurls",
}
REQUEST_IMAGE_BASE64_KEYS = {
    "base64_image",
    "base64_images",
    "base64image",
    "base64images",
    "input_image_base64",
    "input_image_base64s",
    "inputimagebase64",
    "inputimagebase64s",
}


class LogService:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.store = SQLiteLogStore(path.with_suffix(".db"), path)

    def add(self, type: str, summary: str = "", detail: dict[str, Any] | None = None, **data: Any) -> None:
        item = {
            "id": uuid4().hex,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "type": type,
            "summary": summary,
            "detail": detail or data,
        }
        self.store.append(item)

    def list(
        self,
        type: str = "",
        start_date: str = "",
        end_date: str = "",
        page: int = 1,
        page_size: int = 20,
        key_name: str = "",
        account_email: str = "",
        status: str = "",
        summary: str = "",
    ) -> dict[str, Any]:
        """分页查询日志，倒序（最新在前）。列表接口不返回大字段。"""
        result = self.store.list(
            type=type,
            start_date=start_date,
            end_date=end_date,
            page=page,
            page_size=page_size,
            key_name=key_name,
            account_email=account_email,
            status=status,
            summary=summary,
        )
        for item in result["items"]:
            detail = item.get("detail")
            if isinstance(detail, dict) and any(key in detail for key in LOG_LIST_OMITTED_DETAIL_KEYS):
                item["detail"] = {key: value for key, value in detail.items() if key not in LOG_LIST_OMITTED_DETAIL_KEYS}
        return result

    def get_by_id(self, log_id: str) -> dict[str, Any] | None:
        """根据 ID 查询单条日志完整数据（含 request_text）。"""
        return self.store.get_by_id(log_id)

    def delete(self, ids: list[str]) -> dict[str, int]:
        return {"removed": self.store.delete(ids)}


log_service = LogService(DATA_DIR / "logs.jsonl")


def _collect_urls(value: object) -> list[str]:
    urls: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "url" and isinstance(item, str):
                urls.append(item)
            elif key == "urls" and isinstance(item, list):
                urls.extend(str(url) for url in item if isinstance(url, str))
            else:
                urls.extend(_collect_urls(item))
    elif isinstance(value, list):
        for item in value:
            urls.extend(_collect_urls(item))
    return urls


def _add_unique_url(urls: list[str], url: str) -> None:
    value = str(url or "").strip()
    if value and value not in urls and len(urls) < MAX_REQUEST_IMAGE_URLS:
        urls.append(value)


def _save_request_image(data: bytes, base_url: str) -> str:
    if not data:
        return ""
    try:
        return image_storage_service.save(data, base_url or None).url
    except Exception:
        return ""


def _decode_data_image_url(value: str) -> bytes:
    header, separator, payload = value.partition(",")
    if not separator or not header.lower().startswith("data:image/") or ";base64" not in header.lower():
        return b""
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return b""


def _decode_base64_image_text(value: object) -> bytes:
    text = str(value or "").strip()
    if not text:
        return b""
    if text.lower().startswith("data:image/"):
        return _decode_data_image_url(text)
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return b""


def _collect_request_image_url_value(urls: list[str], value: object, base_url: str) -> None:
    if len(urls) >= MAX_REQUEST_IMAGE_URLS:
        return
    if isinstance(value, list):
        for item in value:
            _collect_request_image_url_value(urls, item, base_url)
        return
    if isinstance(value, dict):
        _collect_request_image_url_value(urls, value.get("url") or value.get("image_url"), base_url)
        return
    if not isinstance(value, str):
        return
    text = value.strip()
    lower = text.lower()
    if lower.startswith(("http://", "https://")):
        _add_unique_url(urls, text)
        return
    if lower.startswith("data:image/"):
        saved_url = _save_request_image(_decode_data_image_url(text), base_url)
        _add_unique_url(urls, saved_url)


def _collect_request_base64_image_value(urls: list[str], value: object, base_url: str) -> None:
    if len(urls) >= MAX_REQUEST_IMAGE_URLS:
        return
    if isinstance(value, list):
        for item in value:
            _collect_request_base64_image_value(urls, item, base_url)
        return
    if isinstance(value, dict):
        _collect_request_base64_image_value(
            urls,
            value.get("data") or value.get("base64") or value.get("b64_json"),
            base_url,
        )
        return
    if not isinstance(value, str):
        return
    saved_url = _save_request_image(_decode_base64_image_text(value), base_url)
    _add_unique_url(urls, saved_url)


def _collect_request_images_from_value(urls: list[str], value: object, base_url: str, *, image_context: bool = False, key: str = "") -> None:
    if len(urls) >= MAX_REQUEST_IMAGE_URLS:
        return
    normalized_key = key.strip().lower()
    if isinstance(value, list):
        for item in value:
            _collect_request_images_from_value(urls, item, base_url, image_context=image_context, key=key)
        return
    if isinstance(value, str):
        if image_context or normalized_key in REQUEST_IMAGE_URL_KEYS:
            _collect_request_image_url_value(urls, value, base_url)
        if normalized_key in REQUEST_IMAGE_BASE64_KEYS:
            _collect_request_base64_image_value(urls, value, base_url)
        return
    if not isinstance(value, dict):
        return

    item_type = str(value.get("type") or "").strip()
    next_image_context = image_context or item_type in {"image_url", "input_image", "image"}
    if item_type in {"image_url", "input_image", "image"}:
        _collect_request_image_url_value(urls, value.get("image_url") or value.get("url"), base_url)
    if next_image_context:
        inline = value.get("b64_json") or value.get("base64")
        saved_url = _save_request_image(_decode_base64_image_text(inline), base_url) if inline else ""
        _add_unique_url(urls, saved_url)
        source = value.get("source")
        if isinstance(source, dict) and str(source.get("type") or "").strip() == "base64":
            saved_url = _save_request_image(_decode_base64_image_text(source.get("data")), base_url)
            _add_unique_url(urls, saved_url)
        data = value.get("data")
        if isinstance(data, (bytes, bytearray)):
            saved_url = _save_request_image(bytes(data), base_url)
            _add_unique_url(urls, saved_url)

    handled_image_keys = {"image_url", "url", "b64_json", "base64", "source", "data"}
    for child_key, child in value.items():
        normalized_child_key = str(child_key).strip().lower()
        if next_image_context and normalized_child_key in handled_image_keys:
            continue
        if normalized_child_key in REQUEST_IMAGE_URL_KEYS:
            _collect_request_image_url_value(urls, child, base_url)
            continue
        if normalized_child_key in REQUEST_IMAGE_BASE64_KEYS:
            _collect_request_base64_image_value(urls, child, base_url)
            continue
        _collect_request_images_from_value(urls, child, base_url, image_context=next_image_context, key=str(child_key))


def collect_request_image_urls(value: object, base_url: str = "") -> list[str]:
    urls: list[str] = []
    try:
        _collect_request_images_from_value(urls, value, base_url)
    except Exception:
        return urls
    return urls


def collect_request_image_input_urls(images: list[tuple[bytes, str, str]] | None, base_url: str = "") -> list[str]:
    urls: list[str] = []
    for image_data, _filename, _mime_type in images or []:
        if len(urls) >= MAX_REQUEST_IMAGE_URLS:
            break
        try:
            _add_unique_url(urls, _save_request_image(image_data, base_url))
        except Exception:
            continue
    return urls


def _collect_account_emails(value: object) -> list[str]:
    emails: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"_account_email", "account_email"} and isinstance(item, str) and item.strip():
                emails.append(item.strip())
            else:
                emails.extend(_collect_account_emails(item))
    elif isinstance(value, list):
        for item in value:
            emails.extend(_collect_account_emails(item))
    return emails


def _collect_conversation_ids(value: object) -> list[str]:
    ids: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "_conversation_id" and isinstance(item, str) and item.strip():
                ids.append(item.strip())
            else:
                ids.extend(_collect_conversation_ids(item))
    elif isinstance(value, list):
        for item in value:
            ids.extend(_collect_conversation_ids(item))
    return ids


def _collect_cache_hit(value: object) -> bool:
    if isinstance(value, dict):
        if value.get("_cache_hit") is True:
            return True
        return any(_collect_cache_hit(item) for item in value.values())
    if isinstance(value, list):
        return any(_collect_cache_hit(item) for item in value)
    return False


def _strip_internal_response_fields(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _strip_internal_response_fields(item)
            for key, item in value.items()
            if key not in INTERNAL_RESPONSE_KEYS
        }
    if isinstance(value, list):
        return [_strip_internal_response_fields(item) for item in value]
    return value


def _request_excerpt(text: object) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    return " ".join(value.split())


def _clean_response_text(text: object) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    lower = value[:512].lower()
    if "data:image/" in lower and ";base64," in lower:
        return ""
    return value


def _clean_response_delta(text: object) -> str:
    value = str(text or "")
    if not value:
        return ""
    lower = value[:512].lower()
    if "data:image/" in lower and ";base64," in lower:
        return ""
    return value


def _content_text(value: object) -> str:
    if isinstance(value, str):
        return _clean_response_text(value)
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = _clean_response_text(item)
        elif isinstance(item, dict):
            item_type = str(item.get("type") or "").strip()
            text = ""
            if item_type in {"text", "output_text"}:
                text = _clean_response_text(item.get("text"))
            elif not item_type and "text" in item:
                text = _clean_response_text(item.get("text"))
        else:
            text = ""
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _delta_content_text(value: object) -> str:
    if isinstance(value, str):
        return _clean_response_delta(value)
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = _clean_response_delta(item)
        elif isinstance(item, dict):
            item_type = str(item.get("type") or "").strip()
            text = _clean_response_delta(item.get("text")) if item_type in {"text", "output_text"} else ""
        else:
            text = ""
        if text:
            parts.append(text)
    return "".join(parts)


def _choice_message_text(choice: object) -> str:
    if not isinstance(choice, dict):
        return ""
    message = choice.get("message")
    if isinstance(message, dict):
        text = _content_text(message.get("content"))
        if text:
            return text
    delta = choice.get("delta")
    if isinstance(delta, dict):
        return _content_text(delta.get("content"))
    return ""


def _response_output_text(value: object) -> str:
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = _content_text(item.get("content"))
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()


def _collect_response_text(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    choices = value.get("choices")
    if isinstance(choices, list):
        parts = [_choice_message_text(choice) for choice in choices]
        return "\n\n".join(part for part in parts if part).strip()
    output_text = _response_output_text(value.get("output"))
    if output_text:
        return output_text
    content_text = _content_text(value.get("content"))
    if content_text:
        return content_text
    return _clean_response_text(value.get("answer"))


def _collect_stream_response_text(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    choices = value.get("choices")
    if isinstance(choices, list):
        parts = []
        for choice in choices:
            if isinstance(choice, dict) and isinstance(choice.get("delta"), dict):
                text = _delta_content_text(choice["delta"].get("content"))
                if text:
                    parts.append(text)
        return "".join(parts)
    event_type = str(value.get("type") or "")
    if event_type == "response.output_text.delta":
        return _clean_response_delta(value.get("delta"))
    if event_type == "content_block_delta":
        delta = value.get("delta")
        if isinstance(delta, dict) and str(delta.get("type") or "") == "text_delta":
            return _clean_response_delta(delta.get("text"))
    return ""


def _response_excerpt(text: object) -> tuple[str, bool]:
    value = str(text or "").strip()
    if not value:
        return "", False
    if len(value) <= MAX_RESPONSE_TEXT_CHARS:
        return value, False
    return value[:MAX_RESPONSE_TEXT_CHARS], True


def _image_error_response(exc: Exception) -> JSONResponse:
    from services.protocol.conversation import public_image_error_message

    message = public_image_error_message(str(exc))
    if "no available image quota" in message.lower():
        return openai_error_response(
            {
                "error": {
                    "message": "no available image quota",
                    "type": "insufficient_quota",
                    "param": None,
                    "code": "insufficient_quota",
                }
            },
            429,
        )
    if hasattr(exc, "to_openai_error") and hasattr(exc, "status_code"):
        return JSONResponse(status_code=int(exc.status_code), content=exc.to_openai_error())
    return openai_error_response(message, 502)


def _protocol_error_response(exc: Exception, status_code: int, sse: str) -> JSONResponse:
    message = str(exc)
    if sse == "anthropic":
        return anthropic_error_response(message, status_code)
    return openai_error_response(message, status_code)


def _next_item(items):
    try:
        return True, next(items)
    except StopIteration:
        return False, None


@dataclass
class LoggedCall:
    identity: dict[str, object]
    endpoint: str
    model: str
    summary: str
    started: float = field(default_factory=time.time)
    request_text: str = ""
    request_shape: dict[str, int] | None = None
    request_urls: list[str] | None = None

    async def run(self, handler, *args, sse: str = "openai"):
        from services.account_service import AccountModelUnavailableError
        from services.protocol.conversation import ImageGenerationError

        try:
            result = await run_in_threadpool(handler, *args)
        except ImageGenerationError as exc:
            self.log("调用失败", status="failed", error=str(exc), account_email=getattr(exc, "account_email", ""),
                     conversation_id=getattr(exc, "conversation_id", ""))
            return _image_error_response(exc)
        except HTTPException as exc:
            self.log("调用失败", status="failed", error=str(exc.detail))
            raise
        except AccountModelUnavailableError as exc:
            self.log("调用失败", status="failed", error=str(exc))
            return _protocol_error_response(exc, 503, sse)
        except Exception as exc:
            self.log("调用失败", status="failed", error=str(exc), account_email=getattr(exc, "account_email", ""))
            if self.endpoint.startswith("/v1/images"):
                return _image_error_response(exc)
            return _protocol_error_response(exc, 502, sse)

        if isinstance(result, dict):
            self.log("调用完成", result)
            response = _strip_internal_response_fields(result)
            return response if isinstance(response, dict) else result

        sender = anthropic_sse_stream if sse == "anthropic" else sse_json_stream
        try:
            has_first, first = await run_in_threadpool(_next_item, result)
        except ImageGenerationError as exc:
            self.log("调用失败", status="failed", error=str(exc), account_email=getattr(exc, "account_email", ""),
                     conversation_id=getattr(exc, "conversation_id", ""))
            return _image_error_response(exc)
        except HTTPException as exc:
            self.log("调用失败", status="failed", error=str(exc.detail))
            raise
        except AccountModelUnavailableError as exc:
            self.log("调用失败", status="failed", error=str(exc))
            return _protocol_error_response(exc, 503, sse)
        except Exception as exc:
            self.log("调用失败", status="failed", error=str(exc), account_email=getattr(exc, "account_email", ""))
            if self.endpoint.startswith("/v1/images"):
                return _image_error_response(exc)
            return _protocol_error_response(exc, 502, sse)
        if not has_first:
            self.log("流式调用结束")
            return StreamingResponse(sender(()), media_type="text/event-stream")
        return StreamingResponse(sender(self.stream(itertools.chain([first], result))), media_type="text/event-stream")

    def stream(self, items):
        urls: list[str] = []
        account_emails: list[str] = []
        conversation_ids: list[str] = []
        response_parts: list[str] = []
        response_chars = 0
        cache_hit = False
        failed = False
        try:
            for item in items:
                urls.extend(_collect_urls(item))
                account_emails.extend(_collect_account_emails(item))
                conversation_ids.extend(_collect_conversation_ids(item))
                cache_hit = cache_hit or _collect_cache_hit(item)
                response_text = _collect_stream_response_text(item)
                if response_text and response_chars <= MAX_RESPONSE_TEXT_CHARS:
                    remaining = MAX_RESPONSE_TEXT_CHARS + 1 - response_chars
                    response_part = response_text[:remaining]
                    response_parts.append(response_part)
                    response_chars += len(response_part)
                yield _strip_internal_response_fields(item)
        except Exception as exc:
            failed = True
            self.log(
                "流式调用失败",
                status="failed",
                error=str(exc),
                urls=urls,
                account_email=(account_emails[0] if account_emails else getattr(exc, "account_email", "")),
                conversation_id=(conversation_ids[0] if conversation_ids else getattr(exc, "conversation_id", "")),
                response_text="".join(response_parts),
                cache_hit=cache_hit,
            )
            if self.endpoint.startswith("/v1/images") and not hasattr(exc, "to_openai_error"):
                from services.protocol.conversation import ImageGenerationError, public_image_error_message

                raise ImageGenerationError(public_image_error_message(str(exc))) from exc
            raise
        finally:
            if not failed:
                self.log("流式调用结束", urls=urls, account_email=account_emails[0] if account_emails else "",
                         conversation_id=conversation_ids[0] if conversation_ids else "",
                         response_text="".join(response_parts), cache_hit=cache_hit)

    def log(self, suffix: str, result: object = None, status: str = "success", error: str = "",
            urls: list[str] | None = None, account_email: str = "", conversation_id: str = "",
            response_text: str = "", cache_hit: bool = False) -> None:
        detail = {
            "key_id": self.identity.get("id"),
            "key_name": self.identity.get("name"),
            "role": self.identity.get("role"),
            "endpoint": self.endpoint,
            "model": self.model,
            "started_at": datetime.fromtimestamp(self.started).strftime("%Y-%m-%d %H:%M:%S"),
            "ended_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "duration_ms": int((time.time() - self.started) * 1000),
            "status": status,
        }
        request_excerpt = _request_excerpt(self.request_text)
        if request_excerpt:
            detail["request_text"] = request_excerpt
        if self.request_shape:
            detail["request_shape"] = self.request_shape
        if self.request_urls:
            detail["request_urls"] = list(dict.fromkeys(url for url in self.request_urls if url))
        response_excerpt, response_truncated = _response_excerpt(response_text or _collect_response_text(result))
        if response_excerpt:
            detail["response_text"] = response_excerpt
            if response_truncated:
                detail["response_text_truncated"] = True
        if error:
            detail["error"] = error
        email = str(account_email or "").strip()
        if not email:
            emails = _collect_account_emails(result)
            email = emails[0] if emails else ""
        if email:
            detail["account_email"] = email
        conv_id = str(conversation_id or "").strip()
        if not conv_id:
            conv_ids = _collect_conversation_ids(result)
            conv_id = conv_ids[0] if conv_ids else ""
        if conv_id:
            detail["conversation_id"] = conv_id
        if cache_hit or _collect_cache_hit(result):
            detail["cache_hit"] = True
        collected_urls = [*(urls or []), *_collect_urls(result)]
        if collected_urls and not self.endpoint.startswith("/v1/search"):
            detail["urls"] = list(dict.fromkeys(collected_urls))
        log_service.add(LOG_TYPE_CALL, f"{self.summary}{suffix}", detail)
