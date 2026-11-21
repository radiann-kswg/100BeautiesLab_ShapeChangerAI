"""pipeline/prompt_builder.py — キャラクター要約と形状仕様から生成プロンプトを組み立てる。

AGENTS.md「制作と確認」の手順 2 に対応する: 仕上がりの輪郭、縦横比と厚み、顔の位置、
手足・衣装・付属物の見え方を言葉に落とし、途中経過を描く場合は何が変わるかを指定する。
"""

from __future__ import annotations

from typing import Any

from ..shape import objects
from ..shape.modes import appendage_text, limb_text

LAYOUTS = ("single", "sequence", "five-view")

#: レイアウトごとの画像の縦横比（プロバイダ側でサイズへ変換する）。
LAYOUT_ASPECT = {"single": "1:1", "sequence": "3:2", "five-view": "3:2"}


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(item.strip())
    return result


def _ratio(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _measure(value: float | None, cm: float | None) -> str:
    """"0.7（約 106.4 cm）" の形に整える。"""
    if value is None:
        return "—"
    return f"{_ratio(value)}" + (f"（約 {cm:g} cm）" if cm is not None else "")


def _character_block(character: dict[str, Any]) -> list[str]:
    name = character["name_ja"] + (f" / {character['name_en']}" if character.get("name_en") else "")
    lines = ["[キャラクター（変形前の姿）]", f"- 名前: {name}"]
    if character.get("work_title_ja"):
        lines.append(f"- 作品: {character['work_title_ja']}")
    if character.get("form"):
        lines.append(f"- 変形前の形態: {character['form']} form")
    if character.get("height_cm"):
        note = f"（{character['height_note']}）" if character.get("height_note") else ""
        lines.append(f"- 身長: {character['height_cm']:g} cm{note}")
    if character.get("race"):
        lines.append(f"- 種別: {character['race']}")
    features = _unique(
        character["identity_tags"] + character["silhouette_features"]
        + character["form_features"] + character["attached_items"]
    )
    if features:
        lines.append(f"- 外見の特徴: {', '.join(features)}")
    for detail in character["appearance_details"]:
        lines.append(f"- 部位: {detail}")
    for tail in character["tails"]:
        lines.append(f"- 尾の構造: {tail}")
    if character["palette"]:
        lines.append(f"- 配色: {' / '.join(character['palette'])}")
    if not character["has_text_design"]:
        lines.append(
            "- 外見の文字資料が少ないキャラクター。顔立ち・髪・衣装・配色は参照画像に従う。"
        )
    return lines


def state_lines(character: dict[str, Any]) -> list[str]:
    """状態と衣装の指定を、生成と検査の両方に渡す文にする。指定が無ければ空。

    DB の名前で選んだ状態・衣装は、要約の項目がすでにその状態・衣装のものになっている。名前に当たらない
    自由な文は、体型（#Element_BodyType）と衣装・着用物（#Element_CostumeItem）だけを変える。
    """
    lines: list[str] = []
    if character.get("state"):
        lines.append(f"- 状態: {character['state']}（設定資料にある状態）。上の外見の項目は、この状態のもの。")
    if character.get("costume"):
        lines.append(f"- 衣装: {character['costume']}（設定資料にある衣装）。上の外見の項目は、この衣装のもの。")
    if character.get("state_note"):
        lines.append(f"- 状態の指定: {character['state_note']}")
    if character.get("costume_note"):
        lines.append(f"- 衣装の指定: {character['costume_note']}")
    if not lines:
        return []
    if character.get("state_note") or character.get("costume_note"):
        lines.append("- 上の指定で変えてよいのは、体型と衣装・着用物だけ。耳、尾の種類と本数、髪、瞳、番号や紋章は設定のまま保つ。")
        if character.get("state_note"):
            items = " / ".join(character.get("detachable") or [])
            lines.append(f"- 外してよい着用物は、着脱できる設定のもの（{items}）だけ。ほかの衣装は着たままにする。" if items
                         else "- 着脱できる設定の着用物は無い。衣装を脱がせる指定は反映しない。")
    lines.append("- 公式の参照画像と観察は、通常の状態・通常衣装の姿を写している。体型と衣装がこの指定と食い違うときは、"
                 "この指定を採る。顔立ち・髪・配色の基準は参照画像のまま。")
    return ["[状態と衣装の指定（変形前の姿）]", *lines]


def _keep_block(character: dict[str, Any], spec: dict[str, Any] | None = None) -> list[str]:
    if spec and objects.is_object(spec):
        lines = [
            "[物になっても維持する特徴（必須）]",
            "- 髪・肌・衣装の色と柄を、物の表面の配色と模様として残す。色相を変えない。",
            "- 番号・紋章などの識別記号を、物の表面に読める形で残す。同じ記号を 2 か所以上に繰り返さない。",
        ]
        if spec["face"]["mode"] == "keep":
            lines.append("- 顔立ちと瞳の色を変えない。")
        lines += [
            "- 耳の種類、尾の種類と本数を変えず、数えられる形で残す。",
            "Carry the hair, skin and outfit colours and patterns over to the object's surface without shifting"
            " their hues, and keep any number markings or emblems readable on it, each drawn once. Keep the ear"
            " type, tail type and exact tail count countable.",
        ]
        return lines + [f"- {item}" for item in character["immutable_constraints"]]
    lines = [
        "[変形後も維持する特徴（必須）]",
        "- 髪の色と髪型の特徴、瞳の色、顔立ち、衣装の色と柄を変えない。",
        "- 耳の種類、尾の種類と本数、番号・紋章などの識別記号を変えず、数えられる形で残す。重複は禁止するが、対象は各姿・各方向図の1体の内部に限る。別の姿・方向図には必要に応じて同じ記号を描く。",
        "Keep the hair colour, eye colour, facial features, outfit colours and patterns, ear type,"
        " tail type and exact tail count, and any number markings or emblems unchanged and readable."
        " Duplicate markings are prohibited only within each individual figure in each state or view;"
        " repeat the same marking in other states or views as needed.",
    ]
    lines += [f"- {item}" for item in character["immutable_constraints"]]
    return lines


def _object_block(spec: dict[str, Any], references: list[dict[str, Any]] | None) -> list[str]:
    """物品化の変形先。寸法は持たず、その物として読めることと、表面へ移す手がかりを指定する。"""
    name = spec["name_ja"]
    lines = [
        f"[変形後の姿: 物品化「{name}」]",
        f"- 物: {name}。その物として一目で読める輪郭・構造・素材感で描く。人の体の輪郭（頭・胴・肩・腰の形）は残さない。",
        f"  The character has become the object \"{name}\": it must read at a glance as that object in outline,"
        " structure and material, with no human body outline left.",
        "- 配色と模様: 髪・肌・衣装の色と柄を、物の表面の配色と模様に移す。番号や紋章は、物の表面に描く。",
        "  Carry the hair, skin and outfit colours and patterns over to the object's surface; paint any number"
        " markings or emblems on it.",
        f"- 顔: {objects.face_text(spec)[0]}",
        f"  {objects.face_text(spec)[1]}",
    ]
    for limb, label in (("arms", "腕"), ("legs", "脚")):
        text = objects.limb_text(spec, limb)
        lines += [f"- {label}: {text[0]}", f"  {'Arms' if limb == 'arms' else 'Legs'}: {text[1]}"]
    appendage = objects.appendage_text(spec)
    lines += [f"- 耳と尾: {appendage[0]}", f"  {appendage[1]}"]
    lines.append("- 大きさ: 寸法の指定は無い。その物として自然な大きさと比率で描く。")
    if any(ref.get("kind") == "object" for ref in references or []):
        lines.append("- 形・構造・素材感は、添付した「変形先の参考」の画像に合わせる（使い方は [参照画像の扱い]）。")
    return lines


def _shape_block(spec: dict[str, Any]) -> list[str]:
    dims, cms = spec["dimensions"], spec["dimensions_cm"]
    lines = [
        f"[変形後の形状: Mode {spec['mode']:02d} {spec['name_ja']} / {spec['name_en']}]",
        f"- 輪郭: {spec['silhouette_ja']}",
        f"  {spec['silhouette_en']}",
        f"- 顔: {spec['face_ja']}",
        f"  {spec['face_en']}",
        f"- 面と衣装: {spec['surface_ja']}",
        f"  {spec['surface_en']}",
    ]
    for limb, label in (("arms", "腕"), ("legs", "脚")):
        text = limb_text(spec, limb)
        if text:
            lines += [f"- {label}: {text[0]}", f"  {'Arms' if limb == 'arms' else 'Legs'}: {text[1]}"]
    appendage = appendage_text(spec)
    if appendage:
        lines += [f"- 耳と尾: {appendage[0]}", f"  {appendage[1]}"]

    lines.append("- 寸法（変形前の身長を 1 とした比）:")
    if dims["width"] is not None:
        lines.append(
            f"  幅 {_measure(dims['width'], cms['width'])} × 高さ {_measure(dims['height'], cms['height'])}"
            f" × 奥行き {_measure(dims['depth'], cms['depth'])}"
        )
    else:
        lines.append(
            f"  幅は変形前の輪郭の {_ratio(dims['width_factor'])} 倍、高さ {_measure(dims['height'], cms['height'])}、"
            f"奥行き {_measure(dims['depth'], cms['depth'])}"
        )
    if dims["bend"] is not None:
        lines.append(f"  たわみの振れ幅 {_measure(dims['bend'], cms['bend'])}")
    face = spec["face"]
    if face["rule"] in ("head_span", "headed"):
        share = round(face["share_of_shape_height"] * 100)
        lines.append(
            f"- 顔の大きさ: 頭頂から顎下まで（耳を除く）が形状の高さの約 {share}%"
            f" = {_measure(face['span'], face['span_cm'])}"
        )
        if face["rule"] == "headed":
            body = round(face["body_share_of_shape_height"] * 100)
            lines.append(f"- 胴の形状は全高の下側 約 {body}%、頭は上側 約 {share}% を占める。")
    elif face["rule"] == "original_head":
        lines.append("- 顔の大きさ: 変形前と同じ大きさのまま。")
    return lines


def _physique_block(character: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    dims = spec["dimensions"]
    height_cm = spec["before"]["height_cm"]
    height = f"身長 {height_cm:g} cm" if height_cm else "身長は不明（比率だけで指定）"
    lines = [
        "[変形前後の体格の対比]",
        f"- 変形前: {height}。{character['name_ja']} の元の頭身と体型は参照画像と設定に従う。",
    ]
    edges = [v for v in (dims["width"], dims["height"], dims["depth"]) if v is not None]
    if max(edges) == min(edges):
        lines.append(f"- 変形後: 幅・高さ・奥行きはどれも変形前の身長の {_ratio(edges[0])} 倍。")
    else:
        lines.append(
            f"- 変形後: 最も長い辺は変形前の身長の {_ratio(max(edges))} 倍、最も短い辺は {_ratio(min(edges))} 倍。"
            "この縦横比と厚みを守る。"
        )
    if spec["size"] != 1.0:
        lines.append(f"- 形状全体の大きさは既定の {_ratio(spec['size'])} 倍。")
    return lines


def _layout_block(spec: dict[str, Any], layout: str, amount: float) -> list[str]:
    lines = ["[構図]"]
    if objects.is_object(spec):
        return lines + [
            "- 物になった 1 体を、斜め前のやや上から全体が収まるように描く。立体の奥行きと構造が読み取れる角度にする。",
            "- 床に淡い接地影を付け、物が床に置かれていることを示す。",
            "A single object seen from a three-quarter front view, slightly from above, so its depth and"
            " structure read clearly; a soft contact shadow on the floor.",
        ]
    if layout == "sequence":
        lines += [
            "- 横長の 1 枚に、左から「変形前」「途中（変形量 約 50%）」「変形完了」の 3 体を、同じ縮尺・同じ床の線で並べる。",
            "- 3 体は同じキャラクター・同じ衣装・同じ向き（斜め前）。体の間に矢印を置いてよいが、文字は入れない。",
            f"- 同じ縮尺なので、完成形の高さは変形前の身長の約 {_ratio(spec['dimensions']['height'])} 倍に見える。",
            f"- 途中の姿: {spec['midway_ja']}",
            f"  Midway: {spec['midway_en']}",
            "One wide image showing three figures left to right at the same scale on the same ground line:"
            " the original form, the midway stage, and the completed shape.",
        ]
    elif layout == "five-view":
        lines += [
            "- 横長の 1 枚に、変形完了の姿を「正面・右側面・背面・上面・下面」の五方向図として同じ縮尺で並べる。",
            "- 各図は正投影に近い平行な視点で、背景は白。図どうしを重ねない。見出しの文字は入れない。",
            "One wide design sheet showing the completed shape from five directions at the same scale:"
            " front, right side, back, top and bottom, in near-orthographic views on white.",
        ]
    elif amount < 1.0:
        lines += [
            f"- 変形の途中（変形量 約 {round(amount * 100)}%）の 1 体を、斜め前から全体が収まるように描く。",
            f"- 途中の姿: {spec['midway_ja']}",
            f"  Midway: {spec['midway_en']}",
            "- 上の完成寸法へ向かう途中であり、まだ完成形にはしない。",
        ]
    else:
        lines += [
            "- 変形が完了した 1 体を、斜め前のやや上から全体が収まるように描く。立体の奥行きと厚みが読み取れる角度にする。",
            "- 床に淡い接地影を付け、形状が床に置かれていることを示す。",
            "A single completed shape seen from a three-quarter front view, slightly from above,"
            " so its depth and thickness read clearly; a soft contact shadow on the floor.",
        ]
    return lines


ITERATE_ROLE = "以前の生成結果（改稿の起点）"


def _reference_block(references: list[dict[str, Any]] | None, has_revisions: bool = False) -> list[list[str]]:
    """添付する参照の一覧と、役割ごとの使い方。``references`` は、その呼び出しで実際に添付する順に渡す。

    依頼者が渡した画像の役割（``kind``）は ``input_images.ROLES`` にある。
    """
    if not references:
        return []
    lines = ["[参照画像の扱い]"]
    lines += [f"- 参照{i}: {ref['role']}" for i, ref in enumerate(references, start=1)]
    lines += [
        "- 公式の参照画像はキャラクターの外見（顔立ち・髪・衣装・配色）の正典。構図・ポーズ・背景は写さない。",
        "- 参照画像の説明文・ラベル・署名・透かしは描かない。衣装上の公式の番号や紋章は識別要素として残す。",
        "Use the official references only for the character's design. Do not copy their composition, pose,"
        " captions, labels, signatures or watermarks.",
    ]
    first = {}
    for number, ref in enumerate(references, start=1):
        first.setdefault(ref.get("kind"), number)
    if first.keys() & {"base", "markup", "composition", "reference", "object"}:
        lines.append("- キャラクターの外見は公式の参照画像と設定資料が正しい。依頼者が渡したほかの画像と食い違うときは、"
                     "公式の参照画像と設定資料を採る。")
    if "base" in first:
        if "markup" in first:
            rest = "、赤入れの指示" + ("と [前回からの変更点] " if has_revisions else "") + "だけを反映する。"
        elif has_revisions:
            rest = "、[前回からの変更点] だけを反映する。"
        elif "rough" in first:        # 清書。赤入れなどで直した結果は、先に添付したラフにある
            rest = "ぐ。直した点は、検査を通ったラフに従う。"
        else:
            rest = "、下の作画指定に合うように描き直す。"
        lines.append(f"- 参照{first['base']}（加筆の起点）からは構図と良い点を引き継" + ("" if rest[0] == "ぐ" else "ぎ") + rest)
    if "markup" in first:
        target = f"参照{first['base']}（加筆の起点）" if "base" in first else "加筆の起点の絵"
        lines += [
            f"- 参照{first['markup']}（赤入れ）は、{target}に赤で指示を描き込んだもの。赤い線・丸・矢印・短い書き込みが"
            "指す場所を、その指示のとおりに直す。赤い印と指示の文字そのものは絵に描かない。",
            f"Reference {first['markup']} is a marked-up copy of the starting image: fix what the red marks point at,"
            " and never draw the red marks or the handwritten notes themselves.",
        ]
    if "composition" in first:
        lines += [
            f"- 参照{first['composition']}（構図参考）からは、ポーズ・配置・画角だけを借りる。色を抜いてあるので、"
            "色・画風・衣装・顔立ち・キャラクターの外見は写さない。",
            f"Reference {first['composition']} is a greyscale composition guide: borrow only the pose, placement and"
            " camera angle; take no colours, style, outfit or character design from it.",
        ]
    if "object" in first:
        lines += [
            f"- 参照{first['object']}（変形先の参考）は、変形先の物の形・構造・素材感の参考としてだけ使う。"
            "画像の中のロゴ・文字・キャラクターの柄・配色は写さない。配色と模様は、公式の参照画像のキャラクターから取る。",
            f"Reference {first['object']} shows the target object: use it only for the object's shape, structure and"
            " material. Copy no logos, text, character prints or colours from it; take the colours and patterns"
            " from the official character references.",
        ]
    for number, ref in enumerate(references, start=1):
        if ref.get("kind") == "reference":
            lines.append(f"- 参照{number}（参考画像）は「{ref['note']}」のためだけに使う。"
                         "それ以外の要素（キャラクターの外見・構図・画風）は写さない。")
    return [lines]


def _closing_blocks(references: list[dict[str, Any]] | None, scene: str, style: str, extra: str,
                    revisions: list[str] | None, avoid: list[str], is_object: bool = False) -> list[list[str]]:
    blocks: list[list[str]] = []
    if scene:
        blocks.append(["[場面]", f"- {scene}"])
    if extra:
        blocks.append(["[追加の指定]", f"- {extra}"])
    if revisions:
        blocks.append(["[前回からの変更点]"] + [f"- {item}" for item in revisions])
    rules = [
        "[表現の約束]",
        *([
            "- 全年齢向け。物品化は玩具や雑貨のようにコミカルで柔らかく描き、顔を描く場合の表情は穏やかか楽しげにする。",
            "- 痛み・損傷・流血・破れた衣装の表現は入れない。衣装は、色と柄として物の表面に移す。",
            "- 素材感はその物らしく描き（ビニール、布、陶器、木など）、配色と模様は元の髪・肌・衣装から取る。無関係な色や模様を足さない。",
        ] if is_object else [
            "- 全年齢向け。形状変化は玩具やクッションのようにコミカルで柔らかく描き、表情は穏やかか楽しげにする。",
            "- 痛み・損傷・流血・破れた衣装の表現は入れない。衣装は着たまま形状の面に沿わせる。",
            "- 形状の表面は元の肌・髪・衣装の色と質感で構成し、無関係な材質や模様を足さない。",
        ]),
        f"- 画風: {style or ('参照画像の線と塗りに合わせる。' if references else '線のはっきりしたシンプルなアニメ調。')}",
        f"- 背景: {'場面の指定に従う。' if scene else '白の無地。'}",
        "- 画像内に説明文・ラベル・署名・透かしを入れない。",
        "- 場面や追加の指定に書かれていない小物・家具・別のキャラクターを描き足さない。",
        "All-ages, soft and toy-like; no pain, injury, gore or torn clothing. No captions or watermarks."
        " Do not add props, furniture, or extra characters unless requested in the scene or additional instructions.",
    ]
    if avoid:
        rules.append(f"- 混ぜない要素: {', '.join(avoid)}")
    blocks.append(rules)
    return blocks


def _join(blocks: list[list[str]]) -> str:
    return "\n\n".join("\n".join(block) for block in blocks if block) + "\n"


def build_prompt(
    character: dict[str, Any],
    spec: dict[str, Any],
    *,
    layout: str = "single",
    amount: float = 1.0,
    references: list[dict[str, Any]] | None = None,
    scene: str = "",
    style: str = "",
    extra: str = "",
    revisions: list[str] | None = None,
) -> str:
    """キャラクター 1 体ぶんの、生成モデルへ渡すプロンプト本文を返す。"""
    if layout not in LAYOUTS:
        raise ValueError(f"layout は {LAYOUTS} のいずれか: {layout}")
    subject = f"{character['name_ja']}（{character['work_title_ja']}）" if character.get("work_title_ja") else character["name_ja"]
    if objects.is_object(spec):
        blocks: list[list[str]] = [[
            "[依頼]",
            f"{subject} が「{spec['name_ja']}」という物になった姿（物品化）を、同じキャラクターだと分かるように描いてください。",
            f"Draw the character turned into the object \"{spec['name_en']}\", still clearly recognisable as the same"
            " character through its colours, patterns and markings.",
        ]]
        blocks += _reference_block(references, bool(revisions))
        blocks += [_character_block(character), state_lines(character), _keep_block(character, spec),
                   _object_block(spec, references), _layout_block(spec, layout, amount)]
        blocks += _closing_blocks(references, scene, style, extra, revisions,
                                  _unique(character["negative_keywords"]), is_object=True)
        return _join(blocks)
    blocks = [[
        "[依頼]",
        f"{subject} が「{spec['name_ja']}」へ形状変化した姿を、同じキャラクターだと分かるように描いてください。",
        f"Draw the character transformed into the shape \"{spec['name_en']}\" while staying clearly recognisable"
        " as the same character.",
    ]]
    blocks += _reference_block(references, bool(revisions))
    blocks += [_character_block(character), state_lines(character), _keep_block(character), _shape_block(spec),
               _physique_block(character, spec), _layout_block(spec, layout, amount)]
    blocks += _closing_blocks(references, scene, style, extra, revisions, _unique(character["negative_keywords"]))
    return _join(blocks)


def build_joint_prompt(
    subjects: list[tuple[dict[str, Any], dict[str, Any], str]],
    *,
    references: list[dict[str, Any]] | None = None,
    scene: str = "",
    style: str = "",
    extra: str = "",
    revisions: list[str] | None = None,
) -> str:
    """複数キャラクターを 1 枚に描くプロンプト。``subjects`` は (キャラクター要約, 形状仕様, 観察ブロック)。"""
    names = "、".join(f"{character['name_ja']}（{spec['name_ja']}）" for character, spec, _ in subjects)
    blocks: list[list[str]] = [[
        "[依頼]",
        f"形状変化した {len(subjects)} 体のキャラクターを 1 枚に描いてください: {names}。",
        "それぞれが別のキャラクターで、形状もキャラクターごとの指定に従います。特徴を混ぜないでください。",
        "番号・名前の表記・ラベル・タグ・紋章・髪飾りなどの識別記号は、持ち主のキャラクターにだけ描きます。"
        "ほかのキャラクターへ写さず、数も増やしません。",
        f"Draw these {len(subjects)} transformed characters together in one image. Each keeps its own design"
        " and its own shape; do not mix features between them. Number markings, name labels, tags and emblems"
        " belong to one character only: never copy them onto another character.",
    ]]
    blocks += _reference_block(references, bool(revisions))
    avoid: list[str] = []
    for index, (character, spec, observation) in enumerate(subjects, start=1):
        blocks.append([f"===== {index} 体目: {character['name_ja']} ====="])
        blocks += [_character_block(character), state_lines(character), _keep_block(character), _shape_block(spec)]
        if observation:
            blocks.append(observation.split("\n"))
        avoid += character["negative_keywords"]
    blocks.append([
        "[構図]",
        f"- {len(subjects)} 体を同じ縮尺で、同じ床の上に、重ならないように並べる。斜め前のやや上から全員が収まるように描く。",
        "- 各形状の寸法（cm）の比を守り、大きさの違いが分かるようにする。床に淡い接地影を付ける。",
        "All figures at the same scale on the same floor without overlapping, seen from a three-quarter front view.",
    ])
    blocks += _closing_blocks(references, scene, style, extra, revisions, _unique(avoid))
    return _join(blocks)
