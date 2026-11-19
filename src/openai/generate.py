"""openai/generate.py — OpenAI の GPT Image で画像を生成する。

参照画像があれば ``images.edit``（複数画像入力）、なければ ``images.generate`` を使う。
https://developers.openai.com/api/docs/guides/image-generation

環境変数 (.env):
    OPENAI_API_KEY        必須
    OPENAI_IMAGE_MODEL    既定 gpt-image-2.6-sunburst
    OPENAI_IMAGE_QUALITY  low / medium / high / auto（未設定なら API の既定）
"""

from __future__ import annotations

import base64
import os
from io import BytesIO
from pathlib import Path
from typing import Any

from ..utils.image_io import save_image_bytes
from ..utils.provider import GenerationResult, ProviderError, load_references, require_api_key

# gpt-image-1 と dall-e-3 は 2026 年に API から削除済み。GeneratorsAI の DALLE_MODEL /
# OPENAI_CORRECTION_MODEL は読まず、このリポジトリ用の OPENAI_IMAGE_MODEL だけを見る。
DEFAULT_MODEL = "gpt-image-2.6-sunburst"

_SIZES = {"1:1": "1024x1024", "3:2": "1536x1024", "2:3": "1024x1536"}
_QUALITY_ALIASES = {"standard": "medium", "hd": "high"}  # DALL-E 時代の値を読み替える
_SUFFIX = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}


def resolve_model() -> str:
    return os.environ.get("OPENAI_IMAGE_MODEL") or DEFAULT_MODEL


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
        from openai import OpenAI
    except ImportError as err:
        raise ProviderError("openai が未インストールです: pip install -r requirements.txt") from err

    client = OpenAI(api_key=require_api_key("OPENAI_API_KEY"))
    model = resolve_model()
    kwargs: dict[str, Any] = {"model": model, "prompt": prompt, "size": _SIZES[aspect], "n": count}
    quality = (os.environ.get("OPENAI_IMAGE_QUALITY") or "").strip().lower()
    if quality:
        kwargs["quality"] = _QUALITY_ALIASES.get(quality, quality)

    images: list[BytesIO] = []
    for ref, data, mime in load_references(references):
        buffer = BytesIO(data)
        buffer.name = Path(ref["path"]).stem + _SUFFIX.get(mime, ".png")
        images.append(buffer)

    api_mode = "images.edit" if images else "images.generate"
    try:
        if images:
            response = client.images.edit(image=images, **kwargs)
        else:
            response = client.images.generate(**kwargs)
    except Exception as err:  # noqa: BLE001 — SDK の例外型に依存しない
        raise ProviderError(f"OpenAI {api_mode} ({model}) が失敗しました: {err}") from err

    files: list[Path] = []
    for index, item in enumerate(response.data or [], start=1):
        encoded = getattr(item, "b64_json", None)
        if encoded:
            files.append(save_image_bytes(base64.b64decode(encoded), out_dir / f"{stem}_{index:02d}.png"))
    if not files:
        raise ProviderError(f"OpenAI {api_mode} ({model}) から画像データが返りませんでした。")
    return GenerationResult(files=files, model=model, api_mode=api_mode)
