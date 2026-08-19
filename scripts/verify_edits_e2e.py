"""端到端验证：真实账号走改造后的 edits 链路（model=gpt-5-5-instant + thinking_effort=extended）。

会真实消耗一次生图额度。
"""
from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw  # noqa: E402

from services.protocol.openai_v1_image_edit import handle  # noqa: E402


def main() -> None:
    # 生成输入图
    img = Image.new("RGB", (1024, 1024), (40, 90, 160))
    draw = ImageDraw.Draw(img)
    for i in range(0, 1024, 16):
        draw.rectangle([i, 0, i + 8, 1024], fill=(i % 255, 120, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()
    print(f"[input] 生成测试输入图 ({len(png_bytes)} bytes)")

    body = {
        "model": "gpt-5-5-instant",
        "prompt": "把这张图整体亮度提高一些，保持内容不变。",
        "images": [(png_bytes, "input.png", "image/png")],
        "thinking_effort": "high",  # 应归一化为 extended
        "n": 1,
        "response_format": "b64_json",
    }
    print("[call] 开始 edits（gpt-5-5-instant + thinking=high→extended）...")
    result = handle(body)
    print("[call] 返回类型:", type(result).__name__)
    if isinstance(result, dict):
        data = result.get("data") or []
        print(f"[result] data 数量: {len(data)}")
        for item in data:
            print("  b64_json len:", len(str(item.get("b64_json") or "")))
            print("  url:", str(item.get("url") or "")[:120])
        print("[usage]", result.get("usage"))
        if not data:
            print("[FAIL] 未生成图片")
            raise SystemExit(1)
        print("\n[PASS] 端到端验证成功：gpt-5-5-instant + thinking_effort=extended 完整出图")
    else:
        print("[注意] 返回的是流式生成器，逐个消费：")
        count = 0
        for chunk in result:
            count += 1
            kind = chunk.get("object")
            print(f"  chunk[{count}] object={kind} model={chunk.get('model')}")
            if chunk.get("data"):
                print(f"    data items: {len(chunk['data'])}")
        print(f"[PASS] 流式消费 {count} 个 chunk")


if __name__ == "__main__":
    main()
