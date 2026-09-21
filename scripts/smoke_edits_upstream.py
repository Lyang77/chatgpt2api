"""冒烟测试：验证 edits 链路改造后的模型映射 / slug 透传 / 思考归一化 / handler 解析。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.helper import (  # noqa: E402
    DEFAULT_IMAGE_UPSTREAM_MODEL,
    is_upstream_image_model,
    normalize_image_thinking_effort,
    split_image_model,
)
from services.openai_backend_api import OpenAIBackendAPI  # noqa: E402


def check(name: str, got: object, expected: object) -> None:
    ok = got == expected
    print(f"{'PASS' if ok else 'FAIL'} {name}: got={got!r} expected={expected!r}")
    if not ok:
        raise SystemExit(1)


PNG_BYTES = b"x" * 64


def main() -> None:
    # 1) helper 常量与函数
    check("DEFAULT_IMAGE_UPSTREAM_MODEL", DEFAULT_IMAGE_UPSTREAM_MODEL, "gpt-5-6-thinking")
    check("is_upstream_image_model(gpt-5.6-sol)", is_upstream_image_model("gpt-5.6-sol"), True)
    check("is_upstream_image_model(gpt-5-6-thinking)", is_upstream_image_model("gpt-5-6-thinking"), True)
    check("is_upstream_image_model(gpt-5-5-instant)", is_upstream_image_model("gpt-5-5-instant"), True)
    check("is_upstream_image_model(GPT-5-6)", is_upstream_image_model("GPT-5-6"), True)
    check("is_upstream_image_model(gpt-image-2)", is_upstream_image_model("gpt-image-2"), False)
    check("normalize_thinking(high)", normalize_image_thinking_effort("high"), "extended")
    check("normalize_thinking(extended)", normalize_image_thinking_effort("extended"), "extended")
    check("normalize_thinking(none)", normalize_image_thinking_effort("none"), "")
    check("normalize_thinking(empty)", normalize_image_thinking_effort(""), "")
    check("normalize_thinking(None)", normalize_image_thinking_effort(None), "")
    check("split(gpt-5-5) 不识别为标准图片模型", split_image_model("gpt-5-5"), (None, None))

    # 2) 模型 slug 映射
    api = OpenAIBackendAPI(access_token="")
    try:
        check("slug(gpt-image-2) -> gpt-5-6-thinking", api._image_model_slug("gpt-image-2"), "gpt-5-6-thinking")
        check("slug(gpt-5-6-thinking) 透传", api._image_model_slug("gpt-5-6-thinking"), "gpt-5-6-thinking")
        check("slug(gpt-5.6-sol) 透传", api._image_model_slug("gpt-5.6-sol"), "gpt-5.6-sol")
        check("slug(gpt-5-5-instant) 透传", api._image_model_slug("gpt-5-5-instant"), "gpt-5-5-instant")
        check("slug(gpt-5-6) 透传", api._image_model_slug("gpt-5-6"), "gpt-5-6")
        check("slug(codex-gpt-image-2) 保持", api._image_model_slug("codex-gpt-image-2"), "codex-gpt-image-2")
        check("slug(auto)", api._image_model_slug("auto"), "auto")

        # 2b) 思考档位映射（链路层）
        check("map(未传) -> 默认高(extended)", api._map_image_thinking("", "gpt-5-6-thinking"), ("gpt-5-6-thinking", "extended"))
        check("map(high) -> extended", api._map_image_thinking("high", "gpt-5-6-thinking"), ("gpt-5-6-thinking", "extended"))
        check("map(extended) -> extended", api._map_image_thinking("extended", "gpt-5-6-thinking"), ("gpt-5-6-thinking", "extended"))
        check("map(none) -> 关闭", api._map_image_thinking("none", "gpt-5-6-thinking"), ("gpt-5-6-thinking", ""))
        check("map(standard) -> 标准", api._map_image_thinking("standard", "gpt-5-6-thinking"), ("gpt-5-6-thinking", ""))
        check("map(xhigh) -> 极高(max)", api._map_image_thinking("xhigh", "gpt-5-6-thinking"), ("gpt-5-6-thinking", "max"))
        check("map(极高) -> 极高(max)", api._map_image_thinking("极高", "gpt-5-6-thinking"), ("gpt-5-6-thinking", "max"))
        check("map(max) -> 极高(max)", api._map_image_thinking("max", "gpt-5-6-thinking"), ("gpt-5-6-thinking", "max"))
    finally:
        api.close()

    # 3) edits handler 的 model 解析（monkeypatch 底层，不真发上游）
    import services.protocol.openai_v1_image_edit as edit_mod
    captured: dict = {}

    def fake_pool(request):
        captured["model"] = request.model
        captured["upstream_model"] = request.upstream_model
        captured["thinking_effort"] = request.thinking_effort
        captured["conversation_id"] = request.conversation_id
        captured["images"] = bool(request.images)
        return iter(())

    original_pool = edit_mod.stream_image_outputs_with_pool
    edit_mod.stream_image_outputs_with_pool = fake_pool
    try:
        # 4a) model=gpt-5-5-instant + thinking_effort=high
        result = edit_mod.handle({
            "model": "gpt-5-5-instant",
            "prompt": "test",
            "images": [(PNG_BYTES, "a.png", "image/png")],
            "thinking_effort": "high",
            "conversation_id": "conv-abc-123",
        })
        list(result)  # 消费生成器
        check("handler: conv_model 归一化为 gpt-image-2", captured["model"], "gpt-image-2")
        check("handler: upstream_model 透传", captured["upstream_model"], "gpt-5-5-instant")
        check("handler: thinking 原样透传(链路层归一化)", captured["thinking_effort"], "high")
        check("handler: conversation_id 透传", captured["conversation_id"], "conv-abc-123")
        check("handler: images 传递", captured["images"], True)

        # 4b) model=gpt-image-2 默认（无思考）
        edit_mod.handle({
            "model": "gpt-image-2",
            "prompt": "test",
            "images": [(PNG_BYTES, "a.png", "image/png")],
        })
        check("handler: 默认无 upstream_model", captured["upstream_model"], "")
        check("handler: 默认无 thinking", captured["thinking_effort"], "")

        # 4c) reasoning_effort 别名
        edit_mod.handle({
            "model": "gpt-5-6",
            "prompt": "test",
            "images": [(PNG_BYTES, "a.png", "image/png")],
            "reasoning_effort": "extended",
        })
        check("handler: reasoning_effort 别名", captured["thinking_effort"], "extended")

        # 4d) 非法 model 报错
        try:
            edit_mod.handle({"model": "not-a-model", "prompt": "test",
                             "images": [(PNG_BYTES, "a.png", "image/png")]})
            print("FAIL handler: 非法 model 未报错")
            raise SystemExit(1)
        except edit_mod.ImageGenerationError as exc:
            check("handler: 非法 model 400", exc.status_code, 400)
    finally:
        edit_mod.stream_image_outputs_with_pool = original_pool

    print("\nALL SMOKE CHECKS PASSED")


if __name__ == "__main__":
    main()
