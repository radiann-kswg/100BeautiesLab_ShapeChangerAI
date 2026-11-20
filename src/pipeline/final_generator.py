"""pipeline/final_generator.py — Stage 5: 清書と Canva 仕上げ。

Stage 4 を通ったラフ（単体は上位 2 枚、合同は各キャラクター 1 枚）と公式画像を参照に、完成画像を清書する。清書に失敗したら
Stage 4 の画像を完成画像として使う。Canva の連携が設定されていれば、完成画像を Canva の
デザインにして書き出す（省略可）。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

from ..canva import generate as canva
from ..utils.provider import GenerationResult, ProviderError

Generator = Callable[..., GenerationResult]

SINGLE_HEADER = (
    "[清書の依頼]\n"
    "先頭の参照は、検査を通ったラフです。ラフの良い点を統合して、1 枚の完成イラストとして清書してください。\n"
    "形状とキャラクターの識別特徴は下の作画指定を守り、線と塗りは公式画像に合わせます。"
    "ラフに残っている崩れや余計な要素は引き継がないでください。\n\n"
)
JOINT_HEADER = (
    "[清書の依頼]\n"
    "先頭の参照は、キャラクターごとの検査を通ったラフです。それぞれの形状と識別特徴を保ったまま、"
    "全員を 1 枚の完成イラストに描いてください。線と塗りは公式画像に合わせます。"
    "ラフに残っている崩れや余計な要素は引き継がないでください。\n\n"
)


def rough_reference(path: Path, role: str = "検査を通ったラフ") -> dict[str, Any]:
    return {"path": path, "rel": path.name, "role": role, "official": False, "kind": "rough"}


def generate_finals(
    roughs: list[dict[str, Any]],
    *,
    header: str,
    prompt: str,
    references: list[dict[str, Any]],
    stage_dir: Path,
    stem: str,
    aspect: str,
    count: int,
    generator: Generator,
    skip_canva: bool,
    title: str,
    fallback_to_rough: bool = True,
) -> dict[str, Any]:
    """``{"synth", "canva", "all", "notes"}`` を返す。``all`` が最終的な完成画像。"""
    stage_dir.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    try:
        synth = generator(
            header + prompt, references=[*roughs, *references],
            out_dir=stage_dir, stem=stem, aspect=aspect, count=count,
        ).files
    except ProviderError as err:
        if not fallback_to_rough:
            raise
        notes.append(f"清書に失敗したため、Stage 4 の画像を完成画像にしました: {err}")
        synth = []
        for index, source in enumerate((Path(ref["path"]) for ref in roughs[:count]), start=1):
            target = stage_dir / f"{stem}_{index:02d}{source.suffix}"
            shutil.copyfile(source, target)
            synth.append(target)

    exported: list[Path] = []
    selected: list[Path] = []
    if skip_canva:
        notes.append("Canva 仕上げは指定により省略しました。")
        selected = synth
    elif not canva.access_token():
        notes.append(f"Canva 仕上げは {canva.TOKEN_ENV} が未設定のため省略しました。")
        selected = synth
    else:
        for image in synth:
            try:
                image_exports = canva.export_via_canva(image, stage_dir / "canva", title)
                exported += image_exports
                if image_exports:
                    selected.extend(image_exports)
                    continue
            except ProviderError as err:
                notes.append(f"Canva 仕上げに失敗しました（{image.name}）: {err}")
            selected.append(image)
    for note in notes:
        print(f"[INFO] Stage 5: {note}")
    return {"synth": synth, "canva": exported, "all": selected, "notes": notes}
