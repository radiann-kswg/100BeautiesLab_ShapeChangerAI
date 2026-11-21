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

物品化（``--object``）では、形状の指定の代わりに「名前（と参考画像）のとおりの物として読めるか、人の体の輪郭が
残りすぎていないか」を ``shape_issues`` に、配色・模様と顔の扱いの食い違いを ``violations`` に挙げさせる。
尾の本数の照合は、耳と尾の形を保つ指定（``keep``）のときだけ行い、体数は数えない。

依頼者の入力画像のうち、検査と修正に渡すのは赤入れ（``markup``）と、物品化の変形先の参考（``object``）だけ。赤入れがあれば、印の場所が
直っているか、印や指示の文字がラフに残っていないかも検査する。プロンプトは、その呼び出しで添付する
参照の並びに合わせて ``compose`` で組み直す（参照の番号と役割をずらさないため）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..shape import cues as cue_rules
from ..shape import objects
from ..shape.modes import appendage_text, limb_text
from ..utils.provider import GenerationResult, ProviderError
from .design_reference import Vision, observation_block
from .input_images import for_stage
from .prompt_builder import state_lines

Generator = Callable[..., GenerationResult]
#: 添付する参照の並びから、作画指定のプロンプトを組む関数。
Compose = Callable[[list[dict[str, Any]]], str]

SEVERE_THRESHOLD = 3
_ISSUE_KEYS = ("shape_issues", "violations", "missing", "composition_issues")
_LAYOUT_NOTE = {
    "single": "1 体だけを描いた絵。",
    "sequence": "左から「変形前」「途中」「変形完了」の 3 体を並べた絵。形状の指定は右端の完成形に当てはめる。",
    "five-view": "完成形を正面・右側面・背面・上面・下面の五方向から描いた設計図。",
}


def _identity_lines(character: dict[str, Any]) -> list[str]:
    identity = [f"- {line}" for line in character["appearance_details"]]
    identity += [f"- 尾の構造: {line}" for line in character["tails"]]
    if character["palette"]:
        identity.append(f"- 配色: {' / '.join(character['palette'])}")
    if not identity:
        identity.append("- 文字の設定資料が少ないキャラクター。公式画像と下の観察を基準にする。")
    return identity


def _layout_note(spec: dict[str, Any], layout: str) -> str:
    if cue_rules.is_scene(spec):
        return "1 体を、場面の中に溶け込ませて描いた絵。場面の物と重なったり、同じ種類の物が周りにあったりしてよい。"
    return _LAYOUT_NOTE[layout]


def _cue_lines(spec: dict[str, Any]) -> str:
    """検査に渡す、見分ける手がかり・番号の扱い・場面に溶け込む構図での許容範囲。"""
    lines = [
        "[見分ける手がかり]",
        f"- {cue_rules.names(spec)}",
        "- 手がかりが見えない、ほかの物に隠れている、崩れて読めない場合は、missing に「手がかり: 〜」として入れる。",
    ]
    if cue_rules.has(spec, "number"):
        lines.append("- 番号・紋章は、読める位置に、設定の字形で描かれていること。無ければ missing に入れる。")
    else:
        lines.append("- 番号・紋章は、この絵の手がかりではない。見えないこと、小さくて読めないこと、設定で服や髪の陰に"
                     "隠れている番号が描かれていないことは、missing にも「手がかり: 〜」にも入れない。変形前の姿での大きさの比より"
                     "明らかに大きい、設定に無い目立つ場所へ移っている場合だけ、violations に「番号: 〜」として入れる。")
    if cue_rules.is_scene(spec):
        lines += [
            "[構図: 場面に溶け込む]",
            "- 場面の物と重なる、間に挟まる、一部が隠れるのは指定どおり。輪郭の 3 分の 1 くらいまで隠れていても、"
            "見切れや重なりとして composition_issues に入れない。それ以上隠れて形が読めなければ shape_issues に入れる。",
            "- このキャラクターの顔・配色・番号が、場面のほかの物にも描かれていれば、violations に「取り違え: 〜」として入れる。",
            "- このキャラクターが変わった姿が 2 つ以上描かれていれば（表と裏を別の物として並べた場合を含む）、"
            "composition_issues に「体数: 〜」として入れる。",
            "- 床の接地影が無いこと、場面の物との大きさの関係は問題にしない。寸法は形の縦横比と厚みとして読む。",
            "- 手がかりに選んでいない特徴（尾の本数など）が、場面の物に隠れて確認できないことは、missing に入れない。",
        ]
    return "\n".join(lines)


def _object_inspection_prompt(character: dict[str, Any], spec: dict[str, Any], design: dict[str, Any],
                              has_official: bool, has_markup: bool, has_object_image: bool) -> str:
    """物品化のラフの検査。寸法は無いので、物として読めるかと、表面へ移した手がかりを見る。"""
    name, face = spec["name_ja"], spec["face"]["mode"]
    target = [
        f"- 物: {name}",
        "- 配色と模様: 髪・肌・衣装の色と柄を、物の表面の配色と模様に移す。番号や紋章は物の表面に描く。",
        f"- 顔: {objects.face_text(spec)[0]}",
        f"- 腕: {objects.limb_text(spec, 'arms')[0]}",
        f"- 脚: {objects.limb_text(spec, 'legs')[0]}",
        f"- 耳と尾: {objects.appendage_text(spec)[0]}",
    ]
    face_rule = ("顔が無い、顔立ちや瞳の色が公式画像と違う場合は「顔: 〜」" if face == "keep"
                 else "顔が描かれていれば「顔: 描かない指定」")
    parts = [
        f"あなたはイラストの検査係です。1 枚目の画像は、{character['name_ja']} が"
        f"「{name}」という物になった姿（物品化）として生成されたラフです。{_layout_note(spec, 'single')}",
        "[変形先の物の指定]\n" + "\n".join(target),
        "[キャラクターの識別特徴（物の表面へ移す手がかり）]\n" + "\n".join(_identity_lines(character)),
        _cue_lines(spec),
        "\n".join(state_lines(character) + ["- この指定で変えた体型と衣装は、公式画像と違っていても violations や missing に入れない。"]
                  if state_lines(character) else []),
        observation_block(design),
        "[検査のしかた]\n"
        f"- shape_issues: 「{name}」として一目で読めない"
        + ("、変形先の参考の画像と形や構造が大きく違う" if has_object_image else "")
        + "、人の体の輪郭（頭・胴・肩・腰の形）が残りすぎている、腕・脚・耳・尾の扱いが指定と違う場合に入れる。\n"
        "- violations: 物の表面の配色や模様が、キャラクターの髪・肌・衣装の色と柄から取られていなければ「配色: 〜」、"
        f"{face_rule}、設定に無い装飾の描き足しは「蛇足: 〜」、説明文・ラベル・透かしは「文字: 〜」として入れる。"
        + ("変形先の参考の画像にあるロゴ・文字・キャラクターの柄や配色が写っていれば「写し込み: 〜」として入れる。"
           if has_object_image else "") + "\n"
        "- missing: "
        + ("番号や紋章が物の表面に無い、位置や字形が違う場合と、" if cue_rules.has(spec, "number") else "")
        + "耳や尾の種類・本数が設定と違う場合に"
        "「〜: 設定ではどうか」の形で入れる。同じ番号や紋章が 2 か所以上に描かれていれば「重複: 〜」として入れる。\n"
        "- composition_issues: 見切れ、物が 2 つ以上描かれている、破綻した描写を入れる。\n"
        "- 指定どおりの変化（体が物の形になる、衣装が色と柄として表面に移る、手足の収納）は問題にしない。"
        "物の素材感（ビニール、布、陶器、木など）、陰影による濃淡、角度の違いも問題にしない。"
        + ("\n- 公式画像は、キャラクターの外見（配色・模様・顔立ち）の照合に使う。" if has_official else "")
        + ("\n- 「変形先の参考」の画像は、物の形・構造・素材感の照合にだけ使う。キャラクターの外見の基準ではない。"
           if has_object_image else "")
        + ("\n- 「赤入れ」の画像は公式画像ではなく、加筆の起点の絵に赤い印で修正の指示を描き込んだもの。"
           "印が指す場所が指示のとおりに直っていなければ「赤入れ未反映: 〜」、赤い印や指示の文字がラフに"
           "残っていれば「赤入れの印: 〜」として composition_issues に入れる。" if has_markup else ""),
        "次の JSON だけを返してください（各配列は日本語の短い文。問題が無ければ空配列）:\n"
        '{"shape_issues": [], "violations": [], "missing": [], "composition_issues": []}',
    ]
    return "\n\n".join(part for part in parts if part)


def _inspection_prompt(character: dict[str, Any], spec: dict[str, Any], layout: str, amount: float,
                       design: dict[str, Any], has_official: bool, has_markup: bool = False,
                       has_object_image: bool = False) -> str:
    if objects.is_object(spec):
        return _object_inspection_prompt(character, spec, design, has_official, has_markup, has_object_image)
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

    identity = _identity_lines(character)

    parts = [
        f"あなたはイラストの検査係です。1 枚目の画像は、{character['name_ja']} が"
        f"「{spec['name_ja']}」へ形状変化した姿として生成されたラフです。{_layout_note(spec, layout)}",
        "[形状の指定]\n" + "\n".join(shape),
        "[キャラクターの識別特徴]\n" + "\n".join(identity),
        _cue_lines(spec),
        "\n".join(state_lines(character) + ["- この指定で変えた体型と衣装は、公式画像と違っていても violations や missing に入れない。"]
                  if state_lines(character) else []),
        observation_block(design),
        "[検査のしかた]\n"
        "- shape_issues: 輪郭が指定の形状でない、縦横比や厚みが大きく違う、顔の位置が違う、"
        "腕・脚・耳・尾の扱いが指定と違う場合に入れる。\n"
        "- violations: 設定に無い装飾や部位の描き足しは「蛇足: 〜」、左右で異なる要素が公式画像と"
        "逆なら「左右反転: 〜」、説明文・ラベル・透かしは「文字: 〜」として入れる。\n"
        "- missing: 識別特徴が無い、数が違う、位置が違う、色相が明らかに違う場合に"
        "「〜: 設定ではどうか」の形で入れる。尾は見えている本数を数え、設定の本数を確認できなければ入れる。"
        + ("番号や紋章は位置と字形を確認し、" if cue_rules.has(spec, "number") else "") + "重複は 1 体の 1 つの姿または五方向図の 1 方向図の内部だけで判定する。"
        "sequence の別の姿や五方向図の別方向図に同じ記号が描かれている場合は重複にしない。\n"
        "- composition_issues: 見切れ、体数の誤り、破綻した描写を入れる。\n"
        "- 指定どおりの変形（顔の拡大、衣装が面に沿う、手足の収納）は問題にしない。"
        "陰影による濃淡、ポーズや角度の違いも問題にしない。"
        + ("\n- 2 枚目以降は公式画像。キャラクターの外見はこれと照合する。" if has_official else "")
        + ("\n- 最後の 1 枚（赤入れ）は公式画像ではなく、加筆の起点の絵に赤い印で修正の指示を描き込んだもの。"
           "印が指す場所が指示のとおりに直っていなければ「赤入れ未反映: 〜」、赤い印や指示の文字がラフに"
           "残っていれば「赤入れの印: 〜」として composition_issues に入れる。" if has_markup else ""),
        "次の JSON だけを返してください（各配列は日本語の短い文。問題が無ければ空配列）:\n"
        '{"shape_issues": [], "violations": [], "missing": [], "composition_issues": []}',
    ]
    return "\n\n".join(part for part in parts if part)


def inspect_joint_mixing(image: Path, characters: list[dict[str, Any]], vision: Vision) -> dict[str, Any]:
    """合同の完成画像で、体数が合っているか、識別記号が持ち主以外のキャラクターに描かれていないかを見る。

    見つけた取り違えと検査状態を返す。直しはしない（記録して、依頼者に見せる）。
    """
    listed = "\n".join(
        f"- {character['name_ja']}: " + (" / ".join(character["appearance_details"]) or "（文字の設定資料なし）")
        for character in characters
    )
    prompt = (
        f"この画像には、次の {len(characters)} 体のキャラクターが、形を変えた姿で 1 体ずつ描かれているはずです。\n"
        f"[キャラクターと識別記号]\n{listed}\n\n"
        "[検査のしかた]\n"
        "- あるキャラクターの番号・名前の表記・ラベル・タグ・紋章・髪飾りが、別のキャラクターにも描かれていれば、"
        "mixed に「<描かれている側の名前> に <持ち主の名前> の <記号>」の形で入れる。\n"
        f"- 描かれているキャラクターが {len(characters)} 体でなければ、mixed に「体数: <何体いるか>。{len(characters)} 体を描く」と入れる。\n"
        "- 設定のうえで両方が持つ特徴（同じ種族の耳、同じ色の髪など）は入れない。形状変化による輪郭の違いも入れない。\n"
        '次の JSON だけを返してください（日本語の短い文。問題が無ければ空配列）: {"mixed": []}'
    )
    try:
        raw = vision(prompt, [("検査対象の完成画像", image)])
    except ProviderError as err:
        print(f"[WARN] Stage 5: {image.name} の取り違えの検査に失敗しました: {err}")
        return {"mixed": [], "skipped": True, "error": str(err)}
    found = raw.get("mixed")
    return {"mixed": [str(item) for item in found if item] if isinstance(found, list) else [],
            "skipped": False}


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


def _measured_issues(measured: dict[str, Any], character: dict[str, Any], result: dict[str, list[str]],
                     spec: dict[str, Any] | None = None) -> None:
    """数えさせた値を設定と照合し、食い違いを指摘に加える（1 体だけを描く絵が対象）。

    物品化では、耳と尾の形を保つ指定のときだけ尾を照合し、体数は照合しない（顔の無い物は 0 体と数えられる）。
    場面に溶け込む構図では、尾を手がかりに選んだときだけ尾を照合し（選ばなければ隠れてよい）、体数は照合しない
    （周りの同じ種類の物まで数えられる）。
    """
    is_object = bool(spec) and objects.is_object(spec)
    scene = bool(spec) and cue_rules.is_scene(spec)
    tails, expected = measured.get("tail_tips_visible"), character.get("tail_count")
    if is_object and spec["appendages"] != "keep":
        expected = None
    if scene and not cue_rules.has(spec, "tail"):
        expected = None
    if isinstance(tails, int) and expected and tails != expected:
        result["missing"].append(
            f"尾: 先端が見えているのは {tails} 本。設定は {expected} 本なので、{expected} 本すべての先端が見えるように描く")
    figures = measured.get("figure_count")
    if isinstance(figures, int) and figures != 1 and not is_object and not scene:
        result["composition_issues"].append(f"体数: {figures} 体描かれている。1 体だけを描く")


def inspect_rough(image: Path, prompt: str, official: list[dict[str, Any]], vision: Vision,
                  character: dict[str, Any], layout: str, markup: dict[str, Any] | None = None,
                  spec: dict[str, Any] | None = None, target: dict[str, Any] | None = None) -> dict[str, Any]:
    """1 枚を検査する。Vision が失敗したら ``skipped`` を立てて返す。``target`` は物品化の変形先の参考。"""
    images = [("検査対象のラフ", image)] + [(f"公式画像: {ref['role']}", ref["path"]) for ref in official[:2]]
    if target:
        images.append(("変形先の参考（物の形と素材感。キャラクターの外見の基準ではない）", target["path"]))
    if markup:
        images.append(("赤入れ（加筆の起点への指示書き。公式画像ではない）", markup["path"]))
    try:
        raw = vision(prompt, images)
    except ProviderError as err:
        return {key: [] for key in _ISSUE_KEYS} | {"overall_ok": True, "skipped": True, "error": str(err)}
    result: dict[str, Any] = {
        key: [str(v) for v in raw.get(key) or [] if v] if isinstance(raw.get(key), list) else []
        for key in _ISSUE_KEYS
    }
    measured = _measure(image, vision) if layout == "single" else {}
    _measured_issues(measured, character, result, spec)
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
    compose: Compose,
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
    """``{"passed", "corrected", "all", "log"}`` を返す。``all`` は問題の少なかった順。

    ``references`` は Stage 3 でラフに添付した参照。作り直しには同じものを、ラフを起点にした修正には
    Stage 4 へ渡す役割の参照（公式画像、赤入れ、物品化の変形先の参考）だけを添付する。
    """
    stage_dir.mkdir(parents=True, exist_ok=True)
    official = [ref for ref in references if ref.get("official")]
    markup = next((ref for ref in references if ref.get("kind") == "markup"), None)
    target = next((ref for ref in references if ref.get("kind") == "object"), None)
    inspection = _inspection_prompt(character, spec, layout, amount, design, bool(official), bool(markup),
                                    bool(target))
    (stage_dir / "inspection_prompt.txt").write_text(inspection, encoding="utf-8")

    passed: list[Path] = []
    corrected: list[Path] = []
    ranked: list[tuple[int, Path]] = []
    log: list[dict[str, Any]] = []
    for index, rough in enumerate(roughs, start=1):
        analysis = inspect_rough(rough, inspection, official, vision, character, layout, markup, spec, target)
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
                        + "\n上の問題を解消して、最初から描き直してください。\n\n" + compose(refs))
            else:
                action, stem = "corrected", f"rough_{index:02d}_corrected"
                refs = [{"path": rough, "rel": rough.name, "role": "修正対象のラフ", "official": False},
                        *for_stage(references, 4)]
                text = ("[修正の依頼]\n参照1 は修正対象のラフです。構図・画風・問題の無い部分はそのまま保ち、"
                        "次の点だけを最小限の変更で直してください。\n" + "\n".join(notes)
                        + "\n\n以下は元の作画指定です。\n\n" + compose(refs))
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
