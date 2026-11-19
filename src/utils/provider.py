"""utils/provider.py — 生成プロバイダ共通の型。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .image_io import load_reference_bytes


class ProviderError(RuntimeError):
    """生成 API を呼べなかった、または画像が返らなかった。"""


@dataclass(frozen=True)
class GenerationResult:
    files: list[Path]
    model: str
    api_mode: str


def load_references(references: list[dict[str, Any]]) -> list[tuple[dict[str, Any], bytes, str]]:
    """参照画像を (参照, バイト列, MIME) で読む。読めない画像は知らせたうえで外す。"""
    loaded: list[tuple[dict[str, Any], bytes, str]] = []
    for ref in references:
        result = load_reference_bytes(ref["path"])
        if result is None:
            print(f"[WARN] 参照画像を読めないため添付しません: {ref['rel']}")
            continue
        loaded.append((ref, result[0], result[1]))
    return loaded


def require_api_key(name: str) -> str:
    key = os.environ.get(name)
    if not key:
        raise ProviderError(
            f"{name} が設定されていません。リポジトリ直下の .env を確認してください"
            "（python-dotenv が未インストールだと .env は読み込まれません: pip install -r requirements.txt）。"
        )
    return key
