"""shape/objects.py — 物品化（家具や道具などの「物」への変形）の仕様。

形状番号（Mode）の変形と違い、シェーダーの寸法を持たない。変形先は名前で指定し、変形先の参考画像
（入力画像の役割 ``object``）は任意。同じキャラクターだと分かる手がかりは、配色と模様（必須）、
顔（``keep`` か ``none``）、耳と尾、腕と脚の扱いで残す。

仕様の辞書は ``modes.build_shape_spec`` と同じキーを持ち、``kind`` が ``"object"`` になる。
寸法（``dimensions``）は None。
"""

from __future__ import annotations

from typing import Any

from .modes import APPENDAGE_CHOICES, LIMB_CHOICES
from .vrc_source import ShapeSourceError

FACE_CHOICES = ("keep", "none")
MAX_NAME = 60

_FACE_TEXT = {
    "keep": (
        "顔（目・口・表情）を、物の正面として自然な位置の表面に 1 つ描く。顔立ちと瞳の色は元のまま。",
        "Draw one face (eyes, mouth, expression) on the surface where the object's front naturally is,"
        " keeping the original facial features and eye colour.",
    ),
    "none": (
        "顔は描かない。配色・模様・識別記号で同じキャラクターだと分かるようにする。",
        "Draw no face; the colours, patterns and markings alone must identify the character.",
    ),
}
_LIMB_TEXT = {
    "keep": ("元の太さのまま物の外へ出す。", "kept at their original thickness, extending outside the object."),
    "fold": ("物の中へ収める。表面には袖や手、靴の色が模様として見える。",
             "folded into the object; the sleeves, hands or shoes show only as colour on its surface."),
    "hide": ("描かない。", "not drawn."),
}
_APPENDAGE_TEXT = {
    "keep": (
        "耳と尾は元の形と本数のまま物の外側へ出し、付け根を物の表面につなぐ。",
        "Ears and tail keep their original form and count, protruding from the object with their bases"
        " attached to its surface.",
    ),
    "integrate": (
        "耳と尾は物の表面へ統合し、面に沿った絵柄として描く。種類と本数が数えられるように描く。",
        "Ears and tail are merged into the object and drawn as artwork lying on its surface, still countable"
        " and recognisable.",
    ),
}


def is_object(spec: dict[str, Any]) -> bool:
    return spec.get("kind") == "object"


def shape_label(spec: dict[str, Any]) -> str:
    """変形先の呼び名（記録と一覧に使う）。形状は「Mode 04 真球」、物品化は「物品化「名前」」。"""
    if is_object(spec):
        return f"物品化「{spec['name_ja']}」"
    return f"Mode {spec['mode']:02d} {spec['name_ja']}"


def build_object_spec(
    name: str,
    *,
    face: str | None = None,
    arms: str | None = None,
    legs: str | None = None,
    appendages: str | None = None,
    height_cm: float | None = None,
) -> dict[str, Any]:
    """物品化 1 つぶんの仕様を組み立てる。腕と脚の既定は収納（fold）、耳と尾の既定は形を保つ（keep）。"""
    label = " ".join(str(name or "").split())
    if not label:
        raise ShapeSourceError("--object には、変形先の物の名前を指定してください（例: シャチ型の浮き輪）。")
    if len(label) > MAX_NAME:
        raise ShapeSourceError(f"--object の名前は {MAX_NAME} 文字までです。細かい指定は --extra に書いてください。")
    for value, choices, flag in ((face, FACE_CHOICES, "--face"), (arms, LIMB_CHOICES, "--arms"),
                                 (legs, LIMB_CHOICES, "--legs"), (appendages, APPENDAGE_CHOICES, "--appendages")):
        if value is not None and value not in choices:
            raise ShapeSourceError(f"{flag} は {' / '.join(choices)} のいずれかを指定してください: {value}")
    return {
        "kind": "object",
        "mode": None,
        "key": "object",
        "name_ja": label,
        "name_en": label,
        "size": 1.0,
        "limbs": {"control": "selectable", "arms": arms or "fold", "legs": legs or "fold"},
        "appendages": appendages or "keep",
        "dimensions": None,
        "dimensions_cm": None,
        "face": {"rule": "object", "mode": face or "keep"},
        "before": {"height_cm": height_cm},
        "warnings": [],
    }


def face_text(spec: dict[str, Any]) -> tuple[str, str]:
    return _FACE_TEXT[spec["face"]["mode"]]


def limb_text(spec: dict[str, Any], limb: str) -> tuple[str, str]:
    return _LIMB_TEXT[spec["limbs"][limb]]


def appendage_text(spec: dict[str, Any]) -> tuple[str, str]:
    return _APPENDAGE_TEXT[spec["appendages"]]
