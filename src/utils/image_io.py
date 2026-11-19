"""utils/image_io.py — 画像バイト列の実体に合わせた保存と、参照画像の読み込み。

生成 API は宣言と異なる形式（PNG 指定で JPEG など）を返すことがあるため、
先頭バイトから形式を判定して拡張子を合わせる。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"GIF87a", ".gif", "image/gif"),
    (b"GIF89a", ".gif", "image/gif"),
)


def detect_image_format(header: bytes) -> tuple[str, str] | None:
    """先頭バイトから (拡張子, MIME) を返す。判別できなければ None。"""
    for magic, ext, mime in _SIGNATURES:
        if header.startswith(magic):
            return ext, mime
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None


def save_image_bytes(data: bytes, desired_path: str | Path) -> Path:
    """実体に合った拡張子で保存し、実際のパスを返す。判別不能なら .png。"""
    target = Path(desired_path)
    detected = detect_image_format(data[:16])
    final = target.with_suffix(detected[0] if detected else ".png")
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(data)
    return final


def load_reference_bytes(path: str | Path, max_side: int = 2048) -> tuple[bytes, str] | None:
    """参照画像を (バイト列, MIME) で返す。長辺が max_side を超える場合は縮小する。

    公式のキャラデザ表には 9000px 級の画像があり、そのまま添付すると API の
    上限を超える。画像として読めないファイルは None を返す（呼び出し側でスキップ）。
    """
    raw = Path(path).read_bytes()
    detected = detect_image_format(raw[:16])
    try:
        from PIL import Image
    except ImportError:
        return (raw, detected[1]) if detected else None
    try:
        with Image.open(BytesIO(raw)) as image:
            if detected and max(image.size) <= max_side:
                return raw, detected[1]
            image.thumbnail((max_side, max_side))
            buffer = BytesIO()
            image.convert("RGBA").save(buffer, format="PNG")
            return buffer.getvalue(), "image/png"
    except Exception:  # noqa: BLE001 — 壊れた画像は参照から外す
        return None
