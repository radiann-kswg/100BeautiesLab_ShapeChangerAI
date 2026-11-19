"""gemini/generate.py — Gemini の画像モデル（Nano Banana）で画像を生成する。

参照画像は役割ラベル付きでプロンプトの後ろに添付する。
https://ai.google.dev/gemini-api/docs/image-generation

環境変数 (.env):
    GEMINI_API_KEY          必須
    GEMINI_IMAGE_MODEL      使用モデル。未設定なら GEMINI_REFERENCE_MODEL、
                            それも無ければ gemini-nano-banana-2.1
    GEMINI_IMAGE_SLEEP      複数枚生成時の待ち秒数（既定 6）
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from ..utils.image_io import save_image_bytes
from ..utils.provider import GenerationResult, ProviderError, load_references, require_api_key

DEFAULT_MODEL = "gemini-nano-banana-2.1"
_RATE_LIMIT_RETRIES = 2
_RATE_LIMIT_WAIT_SEC = 30


def resolve_model() -> str:
    return (
        os.environ.get("GEMINI_IMAGE_MODEL")
        or os.environ.get("GEMINI_REFERENCE_MODEL")
        or DEFAULT_MODEL
    )


def _is_rate_limit(err: Exception) -> bool:
    text = str(err)
    return "429" in text or "RESOURCE_EXHAUSTED" in text


def _image_bytes(response: Any) -> list[bytes]:
    found: list[bytes] = []
    for candidate in getattr(response, "candidates", None) or []:
        for part in getattr(getattr(candidate, "content", None), "parts", None) or []:
            data = getattr(getattr(part, "inline_data", None), "data", None)
            if isinstance(data, (bytes, bytearray)) and data:
                found.append(bytes(data))
    return found


def generate_images(
    prompt: str,
    *,
    references: list[dict[str, Any]],
    out_dir: Path,
    stem: str,
    aspect: str = "1:1",
    count: int = 1,
) -> GenerationResult:
    try:
        from google import genai
        from google.genai import types
    except ImportError as err:
        raise ProviderError("google-genai が未インストールです: pip install -r requirements.txt") from err

    client = genai.Client(api_key=require_api_key("GEMINI_API_KEY"))
    model = resolve_model()
    contents: list[Any] = [types.Part.from_text(text=prompt)]
    attached = 0
    for ref, data, mime in load_references(references):
        attached += 1
        contents.append(types.Part.from_text(text=f"[参照{attached}: {ref['role']}]"))
        contents.append(types.Part.from_bytes(data=data, mime_type=mime))
    config = types.GenerateContentConfig(
        response_modalities=[types.Modality.IMAGE],
        image_config=types.ImageConfig(aspect_ratio=aspect),
    )
    api_mode = "generate_content+references" if attached else "generate_content"
    pause = float(os.environ.get("GEMINI_IMAGE_SLEEP", "").strip() or "6")

    files: list[Path] = []
    for index in range(1, count + 1):
        if index > 1:
            time.sleep(pause)
        for attempt in range(_RATE_LIMIT_RETRIES + 1):
            try:
                response = client.models.generate_content(model=model, contents=contents, config=config)
                break
            except Exception as err:  # noqa: BLE001 — SDK の例外型に依存しない
                if _is_rate_limit(err) and attempt < _RATE_LIMIT_RETRIES:
                    print(f"[WARN] Gemini のレート制限。{_RATE_LIMIT_WAIT_SEC} 秒待って再試行します。")
                    time.sleep(_RATE_LIMIT_WAIT_SEC)
                    continue
                if files:  # 途中まで生成できた分は返し、欠けは呼び出し側が記録する
                    print(f"[WARN] {index} 枚目の生成に失敗しました: {err}")
                    return GenerationResult(files=files, model=model, api_mode=api_mode)
                raise ProviderError(f"Gemini generate_content ({model}) が失敗しました: {err}") from err
        for data in _image_bytes(response)[:1]:
            files.append(save_image_bytes(data, out_dir / f"{stem}_{index:02d}.png"))
    if not files:
        raise ProviderError(
            f"Gemini generate_content ({model}) から画像が返りませんでした"
            "（安全フィルターで止まった可能性があります）。"
        )
    return GenerationResult(files=files, model=model, api_mode=api_mode)
