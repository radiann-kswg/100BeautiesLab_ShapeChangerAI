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


RETRY_HEADER = (
    "[清書のやり直し]\n"
    "前回の清書で、次の問題が見つかりました。これを解消して、最初から清書し直してください。\n"
)

#: 完成画像を見て、やり直しが要る指摘（形状の崩れ）を返す関数。
Review = Callable[[Path], list[str] | None]


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
    review: Review | None = None,
) -> dict[str, Any]:
    """``{"synth", "canva", "all", "notes", "retries"}`` を返す。``all`` が最終的な完成画像。

    ``review`` を渡すと、完成画像ごとに呼び、指摘が返った清書を 1 回だけやり直す。フォールバック画像も検査するが、
    自動のやり直しは行わない。やり直した絵の指摘が減ったときだけ、そちらを完成画像にする（2026-11-21 の 58 のカードで、
    同じ指定でも清書ごとに「体がカードになった絵」と「絵の描かれたカード」に分かれた）。
    """
    stage_dir.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    retries: list[dict[str, Any]] = []
    drawn = True
    try:
        synth = generator(
            header + prompt, references=[*roughs, *references],
            out_dir=stage_dir, stem=stem, aspect=aspect, count=count,
        ).files
    except ProviderError as err:
        if not fallback_to_rough:
            raise
        notes.append(f"清書に失敗したため、Stage 4 の画像を完成画像にしました: {err}")
        synth, drawn = [], False
        for index, source in enumerate((Path(ref["path"]) for ref in roughs[:count]), start=1):
            target = stage_dir / f"{stem}_{index:02d}{source.suffix}"
            shutil.copyfile(source, target)
            synth.append(target)

    for index, image in enumerate(list(synth) if review else []):
        issues = review(image)
        if not issues or not drawn:
            continue
        entry: dict[str, Any] = {"from": image.name, "reason": issues, "kept": image.name}
        retries.append(entry)
        try:
            redo = generator(
                RETRY_HEADER + "\n".join(f"- {issue}" for issue in issues) + "\n\n" + header + prompt,
                references=[*roughs, *references], out_dir=stage_dir,
                stem=f"{stem}_{index + 1:02d}_retry", aspect=aspect, count=1,
            ).files[0]
        except ProviderError as err:
            entry["error"] = str(err)
            notes.append(f"清書のやり直しに失敗したため、最初の清書を使います（{image.name}）: {err}")
            continue
        entry["to"] = redo.name
        redo_issues = review(redo)
        if redo_issues is not None and len(redo_issues) < len(issues):
            synth[index] = redo
            entry["kept"] = redo.name
        notes.append(f"形状の指摘があったため清書をやり直しました（{image.name} → {redo.name}）。"
                     f"完成画像にしたのは {entry['kept']}。")

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
    return {"synth": synth, "canva": exported, "all": selected, "notes": notes, "retries": retries}
