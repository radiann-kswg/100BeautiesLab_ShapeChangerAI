"""shape/cues.py — 構図（composition）と、見分ける手がかり（cues）。

構図は「見せる（showcase）」と「場面に溶け込む（scene）」の 2 つ。場面に溶け込む絵では、変形した姿が
ほかの物に重なったり隠れたりするので、「どこを見ればその子だと分かるか」を手がかりとして選び、
プロンプトと Stage 4 の検査の両方に渡す。合意した要件は docs/requirements-5stage-connector.md の
「構図と複数キャラクターの混在」。
"""

from __future__ import annotations

from typing import Any

COMPOSITIONS = ("showcase", "scene")

#: 手がかりの語 → プロンプトと検査に書く呼び名。
CUES: dict[str, str] = {
    "face": "顔（顔立ちと瞳の色）",
    "palette": "配色（髪・肌・衣装の色と柄）",
    "number": "番号・紋章",
    "ears": "耳",
    "tail": "尾（種類と本数）",
    "hair": "髪のモチーフ（髪型の特徴・髪飾り）",
    "outfit": "衣装の品目（目印になる服や小物）",
}


def parse_cues(raw: str) -> list[str]:
    """``face,palette`` のようなカンマ区切りを、語の並びにする。知らない語は ValueError。"""
    names = [name.strip() for name in raw.split(",") if name.strip()]
    unknown = [name for name in names if name not in CUES]
    if unknown:
        raise ValueError(f"--cues に使えるのは {', '.join(CUES)} です（指定: {', '.join(unknown)}）。")
    return list(dict.fromkeys(names))


def default_cues(spec: dict[str, Any]) -> list[str]:
    """指定が無いときの手がかり。顔（描く形のとき）と配色、形を保つ指定なら耳と尾。

    番号は入れない。選んだときだけ目立たせ、選ばないときは設定の位置と大きさのままにする
    （設定で服の陰に隠れている番号まで、目立つ場所に大きく描かれていたため）。
    """
    cues = ["palette"]
    if (spec.get("face") or {}).get("mode") != "none":
        cues.insert(0, "face")
    if spec.get("appendages", "keep") == "keep":
        cues += ["ears", "tail"]
    return cues


def apply(spec: dict[str, Any], composition: str = "showcase", cues: list[str] | None = None,
          note: str = "") -> dict[str, Any]:
    """形状仕様に、構図と手がかりを書き足して返す。"""
    if composition not in COMPOSITIONS:
        raise ValueError(f"composition は {COMPOSITIONS} のいずれか: {composition}")
    spec["composition"] = composition
    spec["cues"] = list(cues) if cues else default_cues(spec)
    spec["cue_note"] = " ".join(note.split())
    return spec


def is_scene(spec: dict[str, Any]) -> bool:
    return spec.get("composition") == "scene"


def chosen(spec: dict[str, Any]) -> list[str]:
    return spec.get("cues") or default_cues(spec)


def has(spec: dict[str, Any] | None, cue: str) -> bool:
    return bool(spec) and cue in chosen(spec)


def names(spec: dict[str, Any]) -> str:
    """手がかりの呼び名を「、」でつないだもの。補足の文があれば添える。"""
    text = "、".join(CUES[cue] for cue in chosen(spec))
    return f"{text}、{spec['cue_note']}" if spec.get("cue_note") else text
