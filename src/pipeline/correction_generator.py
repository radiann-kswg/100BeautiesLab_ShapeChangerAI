"""pipeline/correction_generator.py — Stage 4: ラフの検査と修正。

各ラフを Vision で公式画像・形状の指定と見比べ、問題を 4 種に分けて挙げさせる:

- ``shape_issues``        形状の指定（輪郭・縦横比と厚み・顔の位置・手足と付属物の扱い）との食い違い
- ``violations``          設定に無い要素、左右反転、画像内の文字
- ``missing``             識別特徴の欠落・数や位置の違い
- ``composition_issues``  構図の破綻

重い食い違い（shape_issues + violations が 3 件以上）は指摘を添えて作り直し、それ未満は
そのラフを起点に画像編集で直す（規則は GeneratorsAI の Stage 4 と同じ）。検査に失敗した
ラフは「未検査」と記録してそのまま通す。

数えれば分かる点（見えている尾の本数、描かれた体数）は、設定を知らせない別の呼び出しで
数えさせ、設定との照合はコードで行う。設定の本数を先に見せると、Vision はその数に合わせて
答えてしまう（2026-11-18 の実行で、尾が 5〜6 本の絵にも「8 本」と答えた）。数え方には
1〜2 本の誤差があるので、指摘は「全部の先端が見えるように描く」という修正の依頼にとどまる。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..shape.modes import appendage_text, limb_text
from ..utils.provider import GenerationResult, ProviderError
from .design_reference import Vision, observation_block

Generator = Callable[..., GenerationResult]

SEVERE_THRESHOLD = 3
_ISSUE_KEYS = ("shape_issues", "violations", "missing", "composition_issues")
_LAYOUT_NOTE = {
    "single": "1 体だけを描いた絵。",
    "sequence": "左から「変形前」「途中」「変形完了」の 3 体を並べた絵。形状の指定は右端の完成形に当てはめる。",
    "five-view": "完成形を正面・右側面・背面・上面・下面の五方向から描いた設計図。",
}


def _inspection_prompt(character: dict[str, Any], spec: dict[str, Any], layout: str, amount: float,
                       design: dict[str, Any], has_official: bool) -> str:
    dims = spec["dimensions"]
    size = (
        f"幅 {dims['width']:g} × 高さ {dims['height']:g} × 奥行き {dims['depth']:g}"
        if dims["width"] is not None
        else f"幅は元の輪郭の {dims['width_factor']:g} 倍、高さ {dims['height']:g}、奥行き {dims['depth']:g}"
    )
    shape = [f"- 輪郭: {spec['silhouette_ja']}", f"- 顔: {spec['face_ja']}", f"- 面と衣装: {spec['surface_ja']}"]
    for limb, label in (("arms", "腕"), ("legs", "脚")):
        text = limb_text(spec, limb)
        if text:
            shape.append(f"- {label}: {text[0]}")
    appendage = appendage_text(spec)
    if appendage:
        shape.append(f"- 耳と尾: {appendage[0]}")
    shape.append(f"- 寸法（変形前の身長を 1 とした比）: {size}")
    if amount < 1.0:
        shape.append(f"- この絵は変形の途中（変形量 約 {round(amount * 100)}%）: {spec['midway_ja']}")

    identity = [f"- {line}" for line in character["appearance_details"]]
    identity += [f"- 尾の構造: {line}" for line in character["tails"]]
    if character["palette"]:
        identity.append(f"- 配色: {' / '.join(character['palette'])}")
    if not identity:
        identity.append("- 文字の設定資料が少ないキャラクター。公式画像と下の観察を基準にする。")

    parts = [
        f"あなたはイラストの検査係です。1 枚目の画像は、{character['name_ja']} が"
        f"「{spec['name_ja']}」へ形状変化した姿として生成されたラフです。{_LAYOUT_NOTE[layout]}",
        "[形状の指定]\n" + "\n".join(shape),
        "[キャラクターの識別特徴]\n" + "\n".join(identity),
        observation_block(design),
        "[検査のしかた]\n"
        "- shape_issues: 輪郭が指定の形状でない、縦横比や厚みが大きく違う、顔の位置が違う、"
        "腕・脚・耳・尾の扱いが指定と違う場合に入れる。\n"
        "- violations: 設定に無い装飾や部位の描き足しは「蛇足: 〜」、左右で異なる要素が公式画像と"
        "逆なら「左右反転: 〜」、説明文・ラベル・透かしは「文字: 〜」として入れる。\n"
        "- missing: 識別特徴が無い、数が違う、位置が違う、色相が明らかに違う場合に"
        "「〜: 設定ではどうか」の形で入れる。尾は見えている本数を数え、設定の本数を確認できなければ入れる。"
        "番号や紋章は位置と字形を確認する。\n"
        "- composition_issues: 見切れ、体数の誤り、破綻した描写を入れる。\n"
        "- 指定どおりの変形（顔の拡大、衣装が面に沿う、手足の収納）は問題にしない。"
        "陰影による濃淡、ポーズや角度の違いも問題にしない。"
        + ("\n- 2 枚目以降は公式画像。キャラクターの外見はこれと照合する。" if has_official else ""),
        "次の JSON だけを返してください（各配列は日本語の短い文。問題が無ければ空配列）:\n"
        '{"shape_issues": [], "violations": [], "missing": [], "composition_issues": []}',
    ]
    return "\n\n".join(part for part in parts if part)


_MEASURE_PROMPT = (
    "この画像に描かれているものを、見えているとおりに数えて答えてください。"
    "推測や、キャラクターの設定についての知識は使わないでください。\n"
    "- tail_tips_visible: 動物の尾の先端を 1 本ずつ数えた本数。先端が画像に見えているものだけを数える（無ければ 0）\n"
    "- figure_count: 描かれているキャラクターの体数\n"
    '次の JSON だけを返してください: {"tail_tips_visible": 0, "figure_count": 1}'
)


def _measure(image: Path, vision: Vision) -> dict[str, Any]:
    """設定を知らせずに数えさせる。失敗したら空（照合しない）。"""
    try:
        return vision(_MEASURE_PROMPT, [("数える対象の画像", image)])
    except ProviderError as err:
        print(f"[WARN] Stage 4: {image.name} の本数を数えられませんでした: {err}")
        return {}


def _measured_issues(measured: dict[str, Any], character: dict[str, Any], result: dict[str, list[str]]) -> None:
    """数えさせた値を設定と照合し、食い違いを指摘に加える（1 体だけを描く絵が対象）。"""
    tails, expected = measured.get("tail_tips_visible"), character.get("tail_count")
    if isinstance(tails, int) and expected and tails != expected:
        result["missing"].append(
            f"尾: 先端が見えているのは {tails} 本。設定は {expected} 本なので、{expected} 本すべての先端が見えるように描く")
    figures = measured.get("figure_count")
    if isinstance(figures, int) and figures != 1:
        result["composition_issues"].append(f"体数: {figures} 体描かれている。1 体だけを描く")


def inspect_rough(image: Path, prompt: str, official: list[dict[str, Any]], vision: Vision,
                  character: dict[str, Any], layout: str) -> dict[str, Any]:
    """1 枚を検査する。Vision が失敗したら ``skipped`` を立てて返す。"""
    images = [("検査対象のラフ", image)] + [(f"公式画像: {ref['role']}", ref["path"]) for ref in official[:2]]
    try:
        raw = vision(prompt, images)
    except ProviderError as err:
        return {key: [] for key in _ISSUE_KEYS} | {"overall_ok": True, "skipped": True, "error": str(err)}
    result: dict[str, Any] = {
        key: [str(v) for v in raw.get(key) or [] if v] if isinstance(raw.get(key), list) else []
        for key in _ISSUE_KEYS
    }
    measured = _measure(image, vision) if layout == "single" else {}
    _measured_issues(measured, character, result)
    result["measurements"] = measured
    result["overall_ok"] = not any(result[key] for key in _ISSUE_KEYS)
    result["skipped"] = False
    return result


def _issue_lines(analysis: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(直すこと, 必ず描くこと) に分ける。"""
    fix = analysis["shape_issues"] + analysis["violations"] + analysis["composition_issues"]
    return fix, analysis["missing"]


def correct_roughs(
    roughs: list[Path],
    *,
    prompt: str,
    character: dict[str, Any],
    spec: dict[str, Any],
    layout: str,
    amount: float,
    design: dict[str, Any],
    references: list[dict[str, Any]],
    stage_dir: Path,
    aspect: str,
    generator: Generator,
    vision: Vision,
) -> dict[str, Any]:
    """``{"passed", "corrected", "all", "log"}`` を返す。``all`` は問題の少なかった順。"""
    stage_dir.mkdir(parents=True, exist_ok=True)
    official = [ref for ref in references if ref.get("official")]
    inspection = _inspection_prompt(character, spec, layout, amount, design, bool(official))
    (stage_dir / "inspection_prompt.txt").write_text(inspection, encoding="utf-8")

    passed: list[Path] = []
    corrected: list[Path] = []
    ranked: list[tuple[int, Path]] = []
    log: list[dict[str, Any]] = []
    for index, rough in enumerate(roughs, start=1):
        analysis = inspect_rough(rough, inspection, official, vision, character, layout)
        fix, must = _issue_lines(analysis)
        issue_count = len(fix) + len(must)
        entry: dict[str, Any] = {"rough_index": index, "file": rough.name, **analysis, "action": "passed"}
        result_path = rough
        if analysis["skipped"]:
            print(f"[WARN] Stage 4: ラフ {index} を検査できませんでした（未検査のまま通します）: {analysis['error']}")
        elif issue_count:
            severe = len(analysis["shape_issues"]) + len(analysis["violations"]) >= SEVERE_THRESHOLD
            notes = []
            if fix:
                notes.append("直すこと:\n" + "\n".join(f"- {item}" for item in fix))
            if must:
                notes.append("必ず描くこと（設定どおりに）:\n" + "\n".join(f"- {item}" for item in must))
            if severe:
                action, stem = "regenerated", f"rough_{index:02d}_regen"
                refs = references
                text = ("[前回のラフで見つかった問題]\n" + "\n".join(notes)
                        + "\n上の問題を解消して、最初から描き直してください。\n\n" + prompt)
            else:
                action, stem = "corrected", f"rough_{index:02d}_corrected"
                refs = [{"path": rough, "rel": rough.name, "role": "修正対象のラフ", "official": False}, *references]
                text = ("[修正の依頼]\n参照1 は修正対象のラフです。構図・画風・問題の無い部分はそのまま保ち、"
                        "次の点だけを最小限の変更で直してください。\n" + "\n".join(notes)
                        + "\n\n以下は元の作画指定です。\n\n" + prompt)
            try:
                made = generator(text, references=refs, out_dir=stage_dir, stem=stem, aspect=aspect, count=1)
                result_path = made.files[0]
                corrected.append(result_path)
                entry.update(action=action, output=result_path.name)
            except ProviderError as err:
                entry.update(action="kept_after_failed_fix", error=str(err))
                print(f"[WARN] Stage 4: ラフ {index} の修正に失敗したため、元のラフを使います: {err}")
        if entry["action"] in ("passed", "kept_after_failed_fix"):
            passed.append(rough)
        # 修正済みは指摘を反映した前提で、問題の無かったラフの次に並べる
        rank = 0 if entry["action"] == "passed" and not issue_count else (
            1 if entry["action"] in ("corrected", "regenerated") else 2 + issue_count)
        ranked.append((rank, result_path))
        log.append(entry)
        print(f"[INFO] Stage 4: ラフ {index} → {entry['action']}（指摘 {issue_count} 件）")

    (stage_dir / "analysis_log.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ordered = [path for _, path in sorted(ranked, key=lambda item: item[0])]
    return {"passed": passed, "corrected": corrected, "all": ordered, "log": log}
