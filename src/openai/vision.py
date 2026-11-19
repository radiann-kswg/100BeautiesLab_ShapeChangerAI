"""openai/vision.py — OpenAI の Vision で画像を読み、JSON で答えを受け取る。

Stage 2（公式設定画の観察）と Stage 4（ラフの検査）が使う。モデルは GeneratorsAI と同じ
``GPT_MODEL``（既定 gpt-4o）。失敗は例外で返し、黙って合格扱いにしない判断は呼び出し側が持つ。
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from ..utils.image_io import load_reference_bytes
from ..utils.provider import ProviderError, require_api_key

DEFAULT_MODEL = "gpt-4o"


def resolve_model() -> str:
    return os.environ.get("GPT_MODEL") or DEFAULT_MODEL


def ask_json(text: str, images: list[tuple[str, Path]]) -> dict[str, Any]:
    """``text`` と、(ラベル, パス) の画像を渡して JSON オブジェクトを受け取る。"""
    try:
        from openai import OpenAI
    except ImportError as err:
        raise ProviderError("openai が未インストールです: pip install -r requirements.txt") from err

    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for label, path in images:
        loaded = load_reference_bytes(path, max_side=1536)
        if loaded is None:
            raise ProviderError(f"画像を読めません: {Path(path).name}")
        data, mime = loaded
        content.append({"type": "text", "text": label})
        content.append({"type": "image_url", "image_url": {
            "url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"}})
    client = OpenAI(api_key=require_api_key("OPENAI_API_KEY"), timeout=120, max_retries=1)
    model = resolve_model()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
        )
        message = response.choices[0].message
        if not message.content:
            raise ProviderError(f"OpenAI Vision ({model}) が回答を返しませんでした: "
                                f"{getattr(message, 'refusal', None) or '理由の記載なし'}")
        result = json.loads(message.content)
    except ProviderError:
        raise
    except Exception as err:  # noqa: BLE001 — SDK の例外型に依存しない
        raise ProviderError(f"OpenAI Vision ({model}) が失敗しました: {type(err).__name__}: {err}") from err
    if not isinstance(result, dict):
        raise ProviderError(f"OpenAI Vision ({model}) の応答が JSON オブジェクトではありません。")
    return result
