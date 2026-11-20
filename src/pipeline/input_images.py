"""pipeline/input_images.py — 依頼者が渡す入力画像（加筆の起点・赤入れ・構図参考・参考画像）。

役割ごとに、枚数の上限と渡すステージが決まっている（docs/requirements-5stage-connector.md）。

| 役割 | 意味 | 枚数 | Stage 3 | Stage 4 | Stage 5 |
| --- | --- | --- | --- | --- | --- |
| ``base`` | 加筆の起点 | 1 | ○（先頭） | × | ○ |
| ``markup`` | ``base`` に描き込んだ赤入れ（``base`` が必須） | 1 | ○ | ○ | × |
| ``composition`` | 構図参考。グレースケールにして渡す | 1 | ○ | × | × |
| ``reference`` | 生成画像と関係のない参考。``note``（用途）が必須 | 3 | ○ | × | ○ |
| ``object`` | 物品化の変形先の参考（``--object`` のときだけ） | 1 | ○ | ○ | ○ |

受け付ける出所（``provenance``）は、このパイプラインの生成結果と依頼者自身の作品だけ
（AGENTS.md「著作物の扱い」）。役割 ``object`` の画像に限り、CC0 とパブリックドメインの素材も受け付け、
出典の URL（``source_url``）とライセンスの表記（``license``）を記録する。置き場は、CLI では references/ か outputs/（``path``）、
コネクタでは GCS の自分の接頭辞の下（``object_key``）に限る。
"""

from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

from ..utils.image_io import detect_image_format

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_NOTE = 200
ASPECT_TOLERANCE = 0.02
ALL_STAGES = (3, 4, 5)
SUFFIXES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
MAX_LICENSE = 200
MAX_URL = 500
PROVENANCES = {
    "own_generated": "このパイプラインの生成結果",
    "own_work": "依頼者自身の作品",
    "own_photo": "依頼者自身が撮った写真",
}
#: 役割 object（物品化の変形先の参考）の画像に限って受け付ける出所。出典の URL とライセンスの表記が要る。
OBJECT_PROVENANCES = {
    "cc0": "CC0 の素材",
    "public_domain": "パブリックドメインの素材",
}
#: 役割 → (プロンプトでの呼び名, 枚数の上限, 渡すステージ)。並びは参照に添付する順。
ROLES: dict[str, tuple[str, int, tuple[int, ...]]] = {
    "base": ("加筆の起点", 1, (3, 5)),
    "markup": ("赤入れ（加筆の起点への指示書き）", 1, (3, 4)),
    "composition": ("構図参考（グレースケール）", 1, (3,)),
    "reference": ("参考画像", 3, (3, 5)),
    "object": ("変形先の参考（物の形と素材感）", 1, (3, 4, 5)),
}
#: 公式画像より前に添付する役割（残りは公式画像の後ろ）。
LEADING = ("base", "markup", "composition")

Locate = Callable[[str], Path]
Fetch = Callable[[str], bytes]


def for_stage(references: list[dict[str, Any]], stage: int) -> list[dict[str, Any]]:
    """そのステージへ渡す参照だけを残す（役割を持たない公式画像などは全ステージ）。"""
    return [ref for ref in references if stage in ref.get("stages", ALL_STAGES)]


def arrange(inputs: list[dict[str, Any]], official: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """添付の順に並べる: 加筆の起点 → 赤入れ → 構図参考 → 公式画像 → そのほか。"""
    lead = sorted((ref for ref in inputs if ref.get("kind") in LEADING), key=lambda ref: LEADING.index(ref["kind"]))
    return lead + official + [ref for ref in inputs if ref.get("kind") not in LEADING]


def parse_spec(value: str) -> list[dict[str, Any]]:
    """``--images`` の値（JSON の配列、またはそれを書いた .json ファイルのパス）を読む。"""
    text = (value or "").strip()
    if not text:
        return []
    if not text.startswith("["):
        file = Path(text).expanduser()
        if not file.is_file():
            raise ValueError(f"--images は JSON の配列か、それを書いたファイルを指定してください: {value}")
        text = file.read_text(encoding="utf-8-sig")
    try:
        items = json.loads(text)
    except json.JSONDecodeError as err:
        raise ValueError(f"--images を JSON として読めません: {err}") from err
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError('--images は [{"role": …, "path" か "object_key": …, "provenance": …}] の形で指定してください。')
    return items


def dimensions(data: bytes) -> tuple[int, int]:
    try:
        from PIL import Image
    except ImportError as err:
        raise ValueError("入力画像の確認には Pillow が必要です: pip install -r requirements.txt") from err
    try:
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            if width * height > MAX_PIXELS:
                raise ValueError(f"画像の画素数が上限（{MAX_PIXELS:,}）を超えています。")
            image.load()
            return width, height
    except ValueError:
        raise
    except Exception as err:  # noqa: BLE001 — 壊れた画像は理由を問わず受け付けない
        raise ValueError("画像として開けません。") from err


def _grayscale(data: bytes) -> bytes:
    """白地に重ねてからグレースケールの PNG にする（透過部分が黒くならないように）。"""
    from PIL import Image

    with Image.open(BytesIO(data)) as image:
        rgba = image.convert("RGBA")
        canvas = Image.new("RGBA", rgba.size, "white")
        canvas.alpha_composite(rgba)
        buffer = BytesIO()
        canvas.convert("L").save(buffer, format="PNG")
        return buffer.getvalue()


def resolve(
    items: list[dict[str, Any]],
    *,
    locate: Locate,
    fetch: Fetch | None,
    iterate_base: Path | None = None,
) -> list[dict[str, Any]]:
    """指定を検証して参照の形にする。API は呼ばない（GCS の読み取りだけ行う）。

    ``locate`` は ``path`` を検証済みの場所へ解決する関数、``fetch`` は ``object_key`` の中身を返す関数。
    ``iterate_base`` は ``--iterate-from`` が決めた加筆の起点（赤入れの縦横比の照合に使う）。
    返す参照の ``path`` は ``materialize`` が実行フォルダーへ書き出すまで None。
    """
    images: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        where = f"images[{index}]"
        role, provenance = str(item.get("role") or ""), str(item.get("provenance") or "")
        note = " ".join(str(item.get("note") or "").split())
        if role not in ROLES:
            raise ValueError(f"{where}: role は {' / '.join(ROLES)} のいずれかを指定してください: {role or '(未指定)'}")
        license_note = " ".join(str(item.get("license") or "").split())
        source_url = str(item.get("source_url") or "").strip()
        if provenance in OBJECT_PROVENANCES and role == "object":
            if not source_url.startswith(("https://", "http://")) or len(source_url) > MAX_URL or not license_note:
                raise ValueError(f"{where}: 出所 {provenance} の画像には、source_url（出典の URL）と license"
                                 "（ライセンスの表記。例: CC0 1.0）が必要です。")
            if len(license_note) > MAX_LICENSE:
                raise ValueError(f"{where}: license は {MAX_LICENSE} 文字までです。")
        elif provenance not in PROVENANCES:
            extra = (f"\n  {' / '.join(OBJECT_PROVENANCES)} は、役割 object（物品化の変形先の参考）の画像だけに使えます。"
                     if provenance in OBJECT_PROVENANCES else "")
            raise ValueError(f"{where}: provenance（出所）は毎回必要です。{' / '.join(PROVENANCES)} のいずれか: "
                             f"{provenance or '(未指定)'}\n  受け付けるのは、このパイプラインの生成結果と依頼者自身の作品だけです。" + extra)
        else:
            license_note = source_url = ""         # 自分の作品と生成結果には、出典の記録は要らない
        if role == "reference" and not note:
            raise ValueError(f"{where}: 役割 reference には note（何の参考にするか）が必要です。")
        if len(note) > MAX_NOTE:
            raise ValueError(f"{where}: note は {MAX_NOTE} 文字までです。")
        path, key = item.get("path"), item.get("object_key")
        if bool(path) == bool(key):
            raise ValueError(f"{where}: path（references/ か outputs/ の中）か object_key（GCS）のどちらか一方を指定してください。")
        if key:
            if fetch is None:
                raise ValueError(f"{where}: object_key を読める GCS の設定がありません。")
            data, source = fetch(str(key)), {"object_key": str(key)}
        else:
            file = locate(str(path))
            if file.stat().st_size > MAX_BYTES:
                raise ValueError(f"{where}: 1 枚 {MAX_BYTES // (1024 * 1024)}MB までです: {path}")
            data, source = file.read_bytes(), {"path": str(file)}
        if len(data) > MAX_BYTES:
            raise ValueError(f"{where}: 1 枚 {MAX_BYTES // (1024 * 1024)}MB までです。")
        detected = detect_image_format(data[:16])
        if not detected or detected[1] not in SUFFIXES:
            raise ValueError(f"{where}: PNG・JPEG・WebP の画像だけを受け付けます（拡張子ではなく中身で判定します）。")
        try:
            width, height = dimensions(data)
        except ValueError as err:
            raise ValueError(f"{where}: {err}") from err
        label, _, stages = ROLES[role]
        images.append({
            "path": None, "rel": next(iter(source.values())), "official": False,
            "role": f"{label}（用途: {note}）" if role == "reference" else label,
            "kind": role, "stages": stages, "note": note, "provenance": provenance, "source": source,
            "license": license_note, "source_url": source_url,
            "data": data, "mime": detected[1], "width": width, "height": height,
            "sha256": hashlib.sha256(data).hexdigest(),
        })

    for role, (label, limit, _) in ROLES.items():
        count = sum(1 for image in images if image["kind"] == role)
        if count > limit:
            raise ValueError(f"役割 {role}（{label}）は {limit} 枚までです（{count} 枚指定されています）。")
    base = next((image for image in images if image["kind"] == "base"), None)
    markup = next((image for image in images if image["kind"] == "markup"), None)
    if markup:
        if base:
            base_size = (base["width"], base["height"])
        elif iterate_base:
            base_size = dimensions(iterate_base.read_bytes())
        else:
            raise ValueError("役割 markup（赤入れ）には、加筆の起点（役割 base の画像か、改稿の起点の実行）が必要です。")
        base_ratio, markup_ratio = base_size[0] / base_size[1], markup["width"] / markup["height"]
        if abs(markup_ratio - base_ratio) / base_ratio >= ASPECT_TOLERANCE:
            raise ValueError(
                f"赤入れの画像の縦横比が加筆の起点と {ASPECT_TOLERANCE:.0%} 以上違います"
                f"（起点 {base_size[0]}×{base_size[1]}、赤入れ {markup['width']}×{markup['height']}）。"
                "起点の画像にそのまま描き込んだものを渡してください。")
    return images


def describe(image: dict[str, Any]) -> dict[str, Any]:
    """記録と事前確認に出す、入力画像 1 枚ぶんの要約（中身は含めない）。"""
    credit = ({"license": image["license"], "source_url": image["source_url"]}
              if image.get("source_url") else {})
    return {
        "role": image["kind"], "note": image["note"], "provenance": image["provenance"], "source": image["source"],
        **credit,
        "bytes": len(image["data"]), "width": image["width"], "height": image["height"], "sha256": image["sha256"],
        "stages": list(image["stages"]), "grayscale": image["kind"] == "composition",
    }


def materialize(images: list[dict[str, Any]], run_dir: Path) -> list[dict[str, Any]]:
    """入力画像を実行フォルダーの inputs/ に書き出して参照の ``path`` を埋め、run_meta 用の記録を返す。

    構図参考はここでグレースケールにする（色・画風を参考にさせないため）。
    """
    records: list[dict[str, Any]] = []
    serial: dict[str, int] = {}
    for image in images:
        kind = image["kind"]
        serial[kind] = serial.get(kind, 0) + 1
        data, suffix = image["data"], SUFFIXES[image["mime"]]
        if kind == "composition":
            data, suffix = _grayscale(data), ".png"
        target = Path(run_dir) / "inputs" / f"{kind}_{serial[kind]:02d}{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        image["path"] = target
        records.append({**describe(image), "file": target.relative_to(run_dir).as_posix()})
    return records
