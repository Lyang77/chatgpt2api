from __future__ import annotations

from io import BytesIO
from typing import Any, Iterator

from PIL import Image

from services.protocol.conversation import (
    ConversationRequest,
    ImageGenerationError,
    ImageOutput,
    collect_image_outputs,
    count_text_tokens,
    encode_images,
    stream_image_chunks,
    stream_image_outputs_with_pool,
)
from utils.helper import (
    is_supported_image_model,
    is_upstream_image_model,
)
from utils.image_tokens import count_image_inputs_tokens, count_image_output_items_tokens, image_usage


def _composite_mask(
    images: list[tuple[bytes, str, str]],
    masks: list[tuple[bytes, str, str]],
) -> list[tuple[bytes, str, str]]:
    """将 mask 的 alpha 通道合成到图片中，标识需要编辑的区域。
    
    mask 的透明区域（低 alpha）= 需要编辑的区域，
    mask 的不透明区域（高 alpha）= 保留的区域。
    如果无 mask 则返回原图。
    """
    if not masks:
        return images
    result: list[tuple[bytes, str, str]] = []
    for i, (data, filename, mime_type) in enumerate(images):
        mask_data = masks[i][0] if i < len(masks) else masks[-1][0]
        img = Image.open(BytesIO(data)).convert("RGBA")
        mask_img = Image.open(BytesIO(mask_data))
        if mask_img.mode == "RGBA":
            alpha = mask_img.split()[3]
        elif mask_img.mode == "L":
            alpha = mask_img
        else:
            alpha = mask_img.convert("L")
        alpha = alpha.resize(img.size, Image.LANCZOS)
        img.putalpha(alpha)
        buf = BytesIO()
        img.save(buf, format="PNG")
        result.append((buf.getvalue(), filename, "image/png"))
    return result


def _with_model(outputs: Iterator[ImageOutput], model: str) -> Iterator[ImageOutput]:
    """修正 ImageOutput 的 model 字段为请求中用户实际指定的模型名。"""
    for output in outputs:
        output.model = model
        yield output


def handle(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    prompt = str(body.get("prompt") or "")
    images = body.get("images") or []
    masks = body.get("mask") or []
    images = _composite_mask(images, masks)
    model = str(body.get("model") or "gpt-image-2").strip()
    # model 支持标准图片模型名（gpt-image-2 / codex-gpt-image-2）或上游生图 slug
    # （gpt-5-5 / gpt-5-5-instant / gpt-5-6 等）。上游 slug 直接透传到上游请求体，
    # 内部账号选择等逻辑仍按 gpt-image-2 处理。
    if not (is_supported_image_model(model) or is_upstream_image_model(model) or model in {"auto"}):
        raise ImageGenerationError(
            f"unsupported image model: {model}",
            status_code=400,
            error_type="invalid_request_error",
            code="unsupported_model",
        )
    upstream_model = model if is_upstream_image_model(model) else ""
    conv_model = "gpt-image-2" if upstream_model else model
    # 思考能力：默认深度思考（与 web 端一致），由链路层归一化；
    # 显式传 thinking_effort=none / reasoning_effort=none 可关闭
    thinking_effort = str(
        (body.get("thinking_effort") if "thinking_effort" in body else body.get("reasoning_effort")) or ""
    ).strip().lower()
    conversation_id = str(body.get("conversation_id") or "").strip()
    n = int(body.get("n") or 1)
    size = body.get("size")
    quality = str(body.get("quality") or "auto")
    output_format = str(body.get("output_format") or "png")
    response_format = str(body.get("response_format") or "b64_json")
    base_url = str(body.get("base_url") or "") or None
    progress_callback = body.get("progress_callback")
    image_task_log_template = body.get("image_task_log_template")
    image_task_batch_id = str(body.get("image_task_batch_id") or "")
    image_result_callback = body.get("image_result_callback")
    wait_for_image_terminal = bool(body.get("wait_for_image_terminal"))
    encoded_images = encode_images(images)
    if not encoded_images:
        raise ImageGenerationError("image is required")
    outputs = stream_image_outputs_with_pool(ConversationRequest(
        prompt=prompt,
        model=conv_model,
        upstream_model=upstream_model,
        thinking_effort=thinking_effort,
        conversation_id=conversation_id,
        n=n,
        size=size,
        quality=quality,
        output_format=output_format,
        response_format=response_format,
        base_url=base_url,
        images=encoded_images,
        message_as_error=True,
        progress_callback=progress_callback,
        image_task_log_template=dict(image_task_log_template) if isinstance(image_task_log_template, dict) else None,
        image_task_batch_id=image_task_batch_id,
        image_result_callback=image_result_callback,
        wait_for_image_terminal=wait_for_image_terminal,
    ))
    if body.get("stream"):
        return stream_image_chunks(_with_model(outputs, model))
    result = collect_image_outputs(outputs)
    result["usage"] = image_usage(
        input_text_tokens=count_text_tokens(prompt, model),
        input_image_tokens=count_image_inputs_tokens(images, model),
        output_tokens=count_image_output_items_tokens(result.get("data"), size, quality),
    )
    return result
