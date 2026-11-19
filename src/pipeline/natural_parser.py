"""pipeline/natural_parser.py — 自然文の依頼を、パイプラインの引数に読み替える。

「85(ハッコ)が箱化している様子」のような文から、キャラクター・形状・構図を LLM に抜き出させる。
複数の形状に読める言葉（箱化、球体化など）は、下の表の代表の形状に決め打ちする。読み替えた
結果は ``interpretation`` として返し、呼び出し側が利用者に示す（違っていれば形状番号で出し直せる）。
"""

from __future__ import annotations

from typing import Any, Callable

from ..shape.modes import APPENDAGE_CHOICES, LIMB_CHOICES, load_mode_descriptions
from ..utils import dataset
from .prompt_builder import LAYOUTS

Ask = Callable[[str, list], dict[str, Any]]

#: 形状を表す言葉 → 代表の形状番号（プロンプトにそのまま載せる）。
SHAPE_WORDS = """\
- 箱・箱化・キューブ・四角くなる → 0
- レンガ・直方体・低い箱 → 1
- 頭はそのままで体だけ箱 → 2
- 膨らむ・膨体・風船のように丸く膨れる → 3
- 球・球体化・ボール・まんまる → 4
- 頭はそのままで体だけ球・2.5 頭身の丸い体 → 5
- 円柱・筒（顔が上の端面にあると明記されているとき） → 6
- 円柱・筒・缶 → 7
- コイン・円盤・メダル → 8
- 床に寝たぺちゃんこ・床に貼り付く → 9
- ぺちゃんこ・平面化・紙のように薄い（人の形のまま） → 10
- 細くなる・細長い平面 → 11
- 横に広がる・幅広の平面 → 12
- 上から押し潰される・プレスされる → 13
- 板・紙・カード・ポスター（四角い一枚になる） → 14
- 反物・布・タオル・巻物 → 15"""


def _instruction(text: str) -> str:
    modes = "\n".join(f"{mode}: {entry['name_ja']}" for mode, entry in sorted(load_mode_descriptions().items()))
    return (
        "次の依頼文から、形状変化イラストの生成に必要な指定を抜き出してください。\n"
        f"[依頼文]\n{text}\n\n"
        f"[形状番号]\n{modes}\n\n"
        "[言葉から形状番号への読み替え]\n形状番号が書かれていればそれを使い、無ければ次の表の代表を選ぶ。\n"
        f"{SHAPE_WORDS}\n\n"
        "[抜き出し方]\n"
        "- subjects: 登場するキャラクターごとに 1 件。character は番号やバッジ（例: 85, 2B）があればそれ、"
        "無ければ書かれている名前。mode は形状番号。形状が 1 つしか書かれていなければ全員に同じ番号を入れる。\n"
        "- layout: 変化の過程・途中経過・ビフォーアフターを求めていれば sequence、"
        "五面図・設定画・複数方向からの図を求めていれば five-view、それ以外は single。\n"
        "- arms / legs: 手足を残す（keep）・しまう（fold）・消す（hide）の指定があれば入れ、無ければ null。\n"
        "- appendages: 耳や尻尾を形に溶け込ませる指定なら integrate、形を保つ指定なら keep、無ければ null。\n"
        "- scene: 場所や背景の指定。無ければ空文字。\n"
        "- extra: 上のどれにも入らない作画の指定。無ければ空文字。\n"
        "次の JSON だけを返してください:\n"
        '{"subjects": [{"character": "85", "mode": 0}], "layout": "single", "arms": null, "legs": null, '
        '"appendages": null, "scene": "", "extra": ""}'
    )


def parse_request(text: str, ask: Ask | None = None, work_key: str | None = None) -> dict[str, Any]:
    """``{"argv": [...], "interpretation": {...}}`` を返す。読み取れなければ ValueError。"""
    if ask is None:
        from ..openai.vision import ask_json as ask
    raw = ask(_instruction(text), [])
    modes = load_mode_descriptions()
    subjects: list[tuple[dict[str, Any], int]] = []
    for item in raw.get("subjects") or []:
        token, mode = str(item.get("character") or "").strip(), item.get("mode")
        if not token or not isinstance(mode, int) or mode not in modes:
            raise ValueError(f"依頼文からキャラクターと形状を読み取れませんでした: {item}")
        subjects.append((dataset.find_any(token, work_key), mode))
    if not subjects:
        raise ValueError("依頼文からキャラクターを読み取れませんでした。番号か名前を含めてください。")

    def badge(record: dict[str, Any]) -> str:
        return dataset.full_badge(record)

    first, first_mode = subjects[0]
    if len(subjects) == 1:
        argv = ["--badge", badge(first), "--work", first["work_key"], "--mode", str(first_mode)]
    else:
        # 合同生成の --joint は作品をまたげないため、作品キーを揃えられるものだけ受け付ける
        works = {record["work_key"] for record, _ in subjects}
        if len(works) > 1:
            raise ValueError("別の作品のキャラクターを 1 枚に描く依頼は、--joint では指定できません。")
        argv = ["--joint", ",".join(f"{badge(record)}:{mode}" for record, mode in subjects),
                "--work", first["work_key"]]
    layout = raw.get("layout") if raw.get("layout") in LAYOUTS else "single"
    if len(subjects) == 1 and layout != "single":
        argv += ["--layout", layout]
    for key, choices in (("arms", LIMB_CHOICES), ("legs", LIMB_CHOICES), ("appendages", APPENDAGE_CHOICES)):
        if raw.get(key) in choices:
            argv += [f"--{key}", raw[key]]
    for key in ("scene", "extra"):
        if isinstance(raw.get(key), str) and raw[key].strip():
            argv.append(f"--{key}={raw[key].strip()}")      # 値が -- で始まっても別の引数として読まれない
    return {
        "argv": argv,
        "interpretation": {
            "subjects": [{"character": dataset.display_name(record), "badge": badge(record),
                          "mode": mode, "shape": modes[mode]["name_ja"]} for record, mode in subjects],
            "layout": layout if len(subjects) == 1 else "single",
            "note": "言葉から形状を読み替えました。違う形状にしたい場合は形状番号を指定してください。",
        },
    }
