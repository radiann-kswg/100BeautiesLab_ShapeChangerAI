"""shape/modes.py — 形状モードの仕様と、変形前後の体格。

言葉の記述は ``mode_descriptions.json``、寸法は :mod:`vrc_source` がシェーダーから
読んだ値を使う。寸法はすべて「変形前の身長を 1 とした比」で、身長（cm）が分かる
キャラクターでは cm も併記する。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .vrc_source import MODE_COUNT, ShaderSource, ShapeSourceError

LIMB_CHOICES = ("keep", "fold", "hide")  # シェーダーの _SCArms / _SCLegs の 0 / 1 / 2
APPENDAGE_CHOICES = ("keep", "integrate")  # _SCAppendage の 0 / 1

_DESCRIPTIONS_PATH = Path(__file__).with_name("mode_descriptions.json")

_LIMB_TEXT = {
    "keep": ("元の太さのまま形状の外へ出す。", "kept at their original thickness, extending outside the shape."),
    "hide": ("描かない。", "not drawn."),
}
_FOLD_TEXT = {
    "arms": (
        "胴体へ収納する。形状の面には手と袖が色として見える。",
        "folded into the body; only the hands and sleeves show as colour on the surface.",
    ),
    "legs": (
        "胴体へ収納する。形状の下面には足裏（靴底）が色として見える。",
        "folded into the body; only the soles show as colour on the underside.",
    ),
}
_APPENDAGE_TEXT = {
    "keep": (
        "耳と尾は元の形と本数のまま形状の外側へ出し、付け根を形状の表面につなぐ。",
        "Ears and tail keep their original form and count, protruding from the shape with their bases attached to its surface.",
    ),
    "integrate": (
        "耳と尾は形状の表面へ統合し、面に沿った絵柄として描く。種類と本数が数えられるように描く。",
        "Ears and tail are merged into the shape and drawn as artwork lying on its surface, still countable and recognisable.",
    ),
}


@lru_cache(maxsize=1)
def load_mode_descriptions() -> dict[int, dict[str, Any]]:
    payload = json.loads(_DESCRIPTIONS_PATH.read_text(encoding="utf-8"))
    modes = {int(entry["mode"]): entry for entry in payload["modes"]}
    if sorted(modes) != list(range(MODE_COUNT)):
        raise ShapeSourceError(f"{_DESCRIPTIONS_PATH.name} に形状 0〜{MODE_COUNT - 1} が揃っていません。")
    return modes


def _nominal_body(source: ShaderSource) -> dict[str, float]:
    """シェーダー既定の名目体格（基準高比）。実アバターでは Setup Avatar が実測で上書きする値。"""
    neck, head, half_width, half_depth = source.prop("_SCMeasure")
    return {
        "neck_height": neck,
        "head_height": head,
        "torso_width": half_width * 2,
        "torso_depth": half_depth * 2,
    }


def _dimensions(entry: dict[str, Any], source: ShaderSource, size: float) -> dict[str, Any]:
    """完成形の外寸（基準高比）。width が None の形状は元の輪郭幅に width_factor を掛ける。"""
    mode = entry["mode"]
    kind = entry["dims"]
    thickness = source.prop("_SCThickness") * size
    consts = source.constants
    dims: dict[str, Any] = {"width": None, "height": None, "depth": None, "width_factor": None, "bend": None}
    if kind == "volume":
        half = source.half_sizes.get(mode)
        if half is None:
            raise ShapeSourceError(f"SCHalfSize() に形状 {mode} の寸法がありません。")
        dims.update(width=half[0] * 2 * size, height=half[1] * 2 * size, depth=half[2] * 2 * size)
    elif kind == "flat_upright":
        factor = consts[entry["width_constant"]] if entry.get("width_constant") else 1.0
        dims.update(width_factor=factor * size, height=size, depth=thickness)
    elif kind == "flat_horizontal":
        dims.update(width_factor=size, height=thickness, depth=size)
    elif kind == "press":
        head_y = source.prop("_SCHead")[1]
        dims.update(
            width=consts["press_half_width"] * 2 * size,
            height=thickness,
            depth=consts["press_half_depth_per_head_y"] * head_y * 2 * size,
        )
    elif kind == "panel":
        is_cloth = entry["key"] == "cloth"
        dims.update(
            width=source.prop("_SCPanelWidth") * size,
            height=(consts["cloth_height"] if is_cloth else 1.0) * size,
            depth=thickness,
            bend=source.prop("_SCClothBend") * size if is_cloth else None,
        )
    else:
        raise ShapeSourceError(f"未知の寸法種別: {kind}")
    return dims


def _face(entry: dict[str, Any], source: ShaderSource, dims: dict[str, Any], size: float,
          nominal: dict[str, float]) -> dict[str, Any]:
    """顔（頭頂〜顎下、耳を除く）の高さ。text の形状は言葉の記述だけを使う。"""
    rule = entry["face_rule"]
    if rule in ("head_span", "headed"):
        divisor = source.constants["head_span_divisor"]
        face = {"rule": rule, "span": dims["height"] / divisor, "share_of_shape_height": 1 / divisor}
        if rule == "headed":
            face["body_share_of_shape_height"] = source.constants["headed_body_fraction"]
        return face
    if rule == "original_head":
        return {"rule": rule, "span": nominal["head_height"] * size}
    return {"rule": rule, "span": None}


def _rounded(values: dict[str, Any]) -> dict[str, Any]:
    return {k: round(v, 4) if isinstance(v, float) else v for k, v in values.items()}


def _to_cm(value: float | None, height_cm: float | None) -> float | None:
    return None if value is None or height_cm is None else round(value * height_cm, 1)


def build_shape_spec(
    mode: int,
    source: ShaderSource,
    *,
    height_cm: float | None = None,
    size: float = 1.0,
    arms: str | None = None,
    legs: str | None = None,
    appendages: str | None = None,
) -> dict[str, Any]:
    """形状 1 つぶんの仕様（記述・四肢と付属物の扱い・変形前後の体格）を組み立てる。"""
    descriptions = load_mode_descriptions()
    if mode not in descriptions:
        raise ShapeSourceError(f"形状番号は 0〜{MODE_COUNT - 1} で指定してください: {mode}")
    low, high = 0.2, 2.0  # _SCSize の Range
    if not low <= size <= high:
        raise ShapeSourceError(f"--size は {low}〜{high} で指定してください: {size}")
    entry = descriptions[mode]
    warnings: list[str] = []

    limbs: dict[str, Any] = {"control": entry["limbs"]}
    if entry["limbs"] == "selectable":
        defaults = {"arms": LIMB_CHOICES[int(source.prop("_SCArms"))], "legs": LIMB_CHOICES[int(source.prop("_SCLegs"))]}
        limbs.update(arms=arms or defaults["arms"], legs=legs or defaults["legs"])
    elif arms or legs:
        warnings.append(f"形状 {mode} は腕・脚の扱いを選べないため、--arms / --legs を無視しました。")

    if entry["appendages"] == "selectable":
        default = APPENDAGE_CHOICES[int(source.prop("_SCAppendage"))]
        appendage_mode = appendages or default
    else:
        appendage_mode = entry["appendages"]
        if appendages:
            warnings.append(f"形状 {mode} は耳・尾も一緒に変形するため、--appendages を無視しました。")

    nominal = _rounded(_nominal_body(source))
    dims = _rounded(_dimensions(entry, source, size))
    face = _rounded(_face(entry, source, dims, size, nominal))
    return {
        **{k: v for k, v in entry.items() if k not in ("dims", "width_constant")},
        "size": size,
        "limbs": limbs,
        "appendages": appendage_mode,
        "dimensions": dims,
        "dimensions_cm": {k: _to_cm(dims[k], height_cm) for k in ("width", "height", "depth", "bend")},
        "face": {**face, "span_cm": _to_cm(face["span"], height_cm)},
        "before": {
            "height_cm": height_cm,
            "nominal_ratios": nominal,
            "nominal_cm": {k: _to_cm(v, height_cm) for k, v in nominal.items()},
        },
        "warnings": warnings,
    }


def limb_text(spec: dict[str, Any], limb: str) -> tuple[str, str] | None:
    """腕（"arms"）・脚（"legs"）の扱いの文（日本語, 英語）。選べない形状では None。"""
    choice = spec["limbs"].get(limb)
    if choice is None:
        return None
    if choice == "fold":
        return (spec["fold_ja"], spec["fold_en"]) if spec.get("fold_ja") else _FOLD_TEXT[limb]
    return _LIMB_TEXT[choice]


def appendage_text(spec: dict[str, Any]) -> tuple[str, str] | None:
    text = _APPENDAGE_TEXT.get(spec["appendages"])
    if text and spec["appendages"] == "keep" and spec.get("keep_note_ja"):  # 形状ごとの補足（膨体・コイン）
        return text[0] + spec["keep_note_ja"], f"{text[1]} {spec['keep_note_en']}"
    return text


#: 紙や板のように薄い形とみなす、最も短い辺の上限（変形前の身長を 1 とした比）。
SHEET_MAX_EDGE = 0.05


def is_sheet(spec: dict[str, Any]) -> bool:
    """紙や板のように薄い形か（コイン、人型平面、低い円盤、板、反物）。物品化は寸法を持たないので False。"""
    dims = spec.get("dimensions") or {}
    edges = [dims.get(key) for key in ("width", "height", "depth")]
    edges = [edge for edge in edges if edge is not None]
    return bool(edges) and min(edges) <= SHEET_MAX_EDGE
