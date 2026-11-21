"""場面に溶け込む構図（--composition scene）と、見分ける手がかり（--cues）の回帰テスト。

    python tests/test_scene_cues.py
    python -m pytest tests/

生成 API も Vision も呼ばず、差し替えた関数で、プロンプト・検査・記録・コネクタの引数を確認する。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.mcp_server import tools  # noqa: E402
from src.pipeline import correction_generator, shape_pipeline  # noqa: E402
from src.shape import cues  # noqa: E402
from src.shape.objects import build_object_spec  # noqa: E402
from test_pipeline import _OK, _FakeGenerator, _fake_vision, _run  # noqa: E402

CARDS = "机の上に崩れたトランプの山。その中の 1 枚として混ざっている"


def test_default_cues_follow_the_shape_and_never_include_the_number():
    sofa = build_object_spec("ソファ")
    assert cues.default_cues(sofa) == ["face", "palette", "ears", "tail"]
    assert cues.default_cues(build_object_spec("ソファ", face="none", appendages="integrate")) == ["palette"]
    try:
        cues.apply(build_object_spec("ソファ", face="none"), cues=["face"])
    except ValueError as err:
        assert "--face none" in str(err) and "--cues face" in str(err)
    else:
        raise AssertionError("顔を描かない指定と face の手がかりを同時に受け付けた")
    assert cues.parse_cues(" face, number ,face ") == ["face", "number"]
    try:
        cues.parse_cues("face,nose")
    except ValueError as err:
        assert "nose" in str(err)
    else:
        raise AssertionError("知らない手がかりを受け付けた")
    applied = cues.apply(sofa, "scene", ["number"], "  前髪の  ハート ")
    assert cues.is_scene(applied) and cues.has(applied, "number") and not cues.has(applied, "face")
    assert cues.names(applied) == "番号・紋章、前髪の ハート"
    assert not cues.has(None, "number") and not cues.is_scene(build_object_spec("ソファ"))   # 構図を付けていない仕様


def test_number_stays_at_its_original_place_unless_it_is_a_chosen_cue():
    _, plain, _ = _run(["--badge", "58", "--mode", "14", "--dry-run"])
    assert "番号・紋章は、設定の位置・大きさ・個数のまま扱う" in plain and "目立つ場所へ移したり大きくしたりしない" in plain
    assert "手がかり: 顔（顔立ちと瞳の色）、配色（髪・肌・衣装の色と柄）。" in plain    # 板になる形は、耳と尾が面の絵柄になる
    _, chosen, _ = _run(["--badge", "58", "--mode", "14", "--cues", "face,number", "--cue-note", "茶色の長い髪",
                         "--dry-run"])
    assert "番号・紋章を、形状の表面の読める位置に、設定の字形のまま残す" in chosen
    assert "手がかり: 顔（顔立ちと瞳の色）、番号・紋章、茶色の長い髪。" in chosen
    _, sofa, _ = _run(["--badge", "57", "--object", "ソファ", "--dry-run"])
    assert "番号や紋章は、元の位置に当たる場所へ、元の大きさのまま移す" in sofa
    _, sofa_number, _ = _run(["--badge", "57", "--object", "ソファ", "--cues", "number", "--dry-run"])
    assert "番号や紋章は、物の表面の読める位置に描く" in sofa_number and "番号・紋章を、物の表面の読める位置に" in sofa_number


def test_scene_composition_blends_into_the_scene_and_drops_the_real_size():
    _, showcase, _ = _run(["--badge", "58", "--mode", "14", "--scene", CARDS, "--dry-run"])
    code, scene, _ = _run(["--badge", "58", "--mode", "14", "--composition", "scene", "--scene", CARDS, "--dry-run"])
    assert code == 0
    assert "床に淡い接地影を付け" in showcase and "約 104.3 cm" in showcase and "[構図: 場面に溶け込む]" not in showcase
    for expected in ("[構図: 場面に溶け込む]", "輪郭の 3 分の 1 くらいまで", "この子の手がかりを写さない",
                     "床の接地影を付けない", "寸法の比（形の縦横比と厚み", "幅 0.7 × 高さ 1 × 奥行き 0.025",
                     "最も長い辺を 1 とすると、最も短い辺は 0.025", "手がかり（配色・顔・番号など）を、場面のほかの物に写さない"):
        assert expected in scene, expected
    assert "約 104.3 cm" not in scene and "床に淡い接地影を付け、" not in scene     # 実寸と固定の置き方は渡さない
    # 2026-11-21 の 58 のカードで、表のカードと裏のカードが 2 枚描かれたラフがあった
    assert "絵の中に 1 つだけ描く。表と裏、別の角度を、別の物として並べない" in scene
    _, floating, _ = _run(["--badge", "2B", "--object", "シャチ型フロート", "--composition", "scene",
                           "--scene", "プールの水面に浮かんでいる", "--dry-run"])
    assert "物になった 1 体を、[場面] の中に溶け込ませて描く" in floating and "その物として自然な比率は守る" in floating


def test_scene_and_cues_are_limited_to_one_finished_figure():
    for argv, word in (
        (["--badge", "58", "--mode", "14", "--composition", "scene"], "--scene"),
        (["--badge", "58", "--mode", "14", "--composition", "scene", "--scene", CARDS, "--layout", "sequence"], "single"),
        (["--badge", "58", "--mode", "14", "--composition", "scene", "--scene", CARDS, "--amount", "0.5"], "single"),
        (["--badge", "58", "--mode", "14", "--cues", "face,nose"], "nose"),
        (["--joint", "85:0,73:3", "--cues", "face"], "1 体"),
        (["--joint", "85:0,73:3", "--composition", "scene", "--scene", CARDS], "1 体"),
    ):
        code, _, err = _run(argv + ["--dry-run"])
        assert code == 2 and word in err, (argv, err[-200:])
    code, out, _ = _run(["--joint", "85:0,73:3", "--dry-run"])
    # 合同は、1 体ずつのラフに既定の手がかりを渡す（選び直しは、まだ受け付けない）
    assert code == 0 and out.count("[見分ける手がかり]") == 2 and "[構図: 場面に溶け込む]" not in out


def test_inspection_reads_the_same_cues_and_allows_overlap_in_a_scene():
    vision = _fake_vision([_OK])
    vision.measurements = [{"tail_tips_visible": 0, "figure_count": 4}]      # 周りのカードまで数え、尾は面の絵柄
    with tempfile.TemporaryDirectory() as tmp:
        code, out, _ = _run(["--badge", "58", "--mode", "14", "--composition", "scene", "--scene", CARDS,
                             "--cues", "face,palette", "--rough-count", "1", "--skip-canva", "--out", tmp],
                            generate=_FakeGenerator(), vision=vision)
        assert code == 0, out
        (run_dir,) = Path(tmp).iterdir()
        meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
        log = json.loads((run_dir / "stage4_correct" / "analysis_log.json").read_text(encoding="utf-8"))
    inspection = next(call["text"] for call in vision.seen if "検査係" in call["text"])
    for expected in ("場面の中に溶け込ませて描いた絵", "[見分ける手がかり]", "顔（顔立ちと瞳の色）、配色（髪・肌・衣装の色と柄）",
                     "番号・紋章は、この絵の手がかりではない", "「番号: 〜」", "「取り違え: 〜」", "「体数: 〜」",
                     "輪郭の 3 分の 1 くらいまで隠れていても", "手がかりに選んでいない特徴"):
        assert expected in inspection, expected
    assert "尾は見えている本数を数え" not in inspection
    assert "体数の誤り" not in inspection
    # 番号を手がかりに選んでいない検査では、番号の位置と字形を確かめさせない（2026-11-21 の 58 のカードで、
    # 選んでいない番号が「手がかり: 番号が見えない」と指摘された）
    assert "番号や紋章は位置と字形を確認し" not in inspection
    assert "「絵の描かれた板: 〜」" in inspection                    # 板の形では、絵が描かれた板になっていないかも見る
    # 尾を手がかりに選んでいないので本数は照合せず、周りの同じ種類の物を体数に数えない
    assert log[0]["action"] == "passed" and log[0]["missing"] == [] and log[0]["composition_issues"] == []
    assert meta["composition"] == "scene"
    assert meta["subjects"][0]["shape"]["composition"] == "scene" and meta["subjects"][0]["shape"]["cues"] == ["face", "palette"]


def test_object_inspection_does_not_count_scene_objects_or_unselected_tails():
    character = {"name_ja": "テスト", "appearance_details": [], "tails": [], "palette": []}
    spec = cues.apply(build_object_spec("ソファ"), "scene", ["face", "palette"])
    inspection = correction_generator._object_inspection_prompt(character, spec, {}, False, False, False)
    assert "場面の周囲にある物の個数は問題にしない" in inspection
    assert "物が 2 つ以上描かれている" not in inspection
    assert "尾の本数は、手がかりに選んでいないので確認しない" in inspection

    spec = cues.apply(build_object_spec("ソファ"), "scene", ["face", "tail"])
    inspection = correction_generator._object_inspection_prompt(character, spec, {}, False, False, False)
    assert "尾の種類・本数が設定と違う場合" in inspection
    assert "尾の本数は、手がかりに選んでいないので確認しない" not in inspection

    spec = cues.apply(build_object_spec("ソファ"), "showcase", ["face", "palette"])
    inspection = correction_generator._object_inspection_prompt(character, spec, {}, False, False, False)
    assert "耳や尾の種類・本数が設定と違う場合" in inspection


def test_measured_counts_respect_the_scene_and_the_tail_cue():
    character, measured = {"tail_count": 8}, {"tail_tips_visible": 3, "figure_count": 5}
    scene = cues.apply(build_object_spec("ソファ"), "scene", ["face", "tail"])
    result = {"missing": [], "composition_issues": []}
    correction_generator._measured_issues(measured, character, result, scene)
    assert result["missing"] and result["composition_issues"] == []          # 尾は手がかり。体数は数えない
    hidden = cues.apply(build_object_spec("ソファ"), "scene", ["face"])
    result = {"missing": [], "composition_issues": []}
    correction_generator._measured_issues(measured, character, result, hidden)
    assert result == {"missing": [], "composition_issues": []}
    number = correction_generator._cue_lines(cues.apply(build_object_spec("ソファ"), "showcase", ["number"]))
    assert "読める位置に、設定の字形で描かれていること" in number and "[構図: 場面に溶け込む]" not in number


def test_thin_shapes_are_the_body_itself_not_a_picture_on_a_card():
    # 2026-11-21 の 58 のカードで、完成画像が「全身の絵が描かれたカード」になった
    rule = "体そのものが、この薄い面になる。キャラクターの全身の絵を印刷したカード・ポスター・看板にしない"
    for mode in ("8", "10", "13", "14", "15"):
        _, out, _ = _run(["--badge", "58", "--mode", mode, "--dry-run"])
        assert rule in out and "小さく描いた全身像・余白・背景・枠を残さない" in out, mode
    _, ball, _ = _run(["--badge", "58", "--mode", "4", "--dry-run"])
    _, sofa, _ = _run(["--badge", "58", "--object", "ソファ", "--dry-run"])
    assert rule not in ball and rule not in sofa
    vision = _fake_vision([_OK])
    with tempfile.TemporaryDirectory() as tmp:
        _run(["--badge", "58", "--mode", "4", "--rough-count", "1", "--skip-canva", "--out", tmp],
             generate=_FakeGenerator(), vision=vision)
    assert "絵の描かれた板" not in next(call["text"] for call in vision.seen if "検査係" in call["text"])


def test_number_inspection_follows_the_cue_and_race_reads_as_text():
    _, chosen, _ = _run(["--badge", "58", "--mode", "14", "--cues", "face,number", "--dry-run"])
    assert "番号・紋章を、形状の表面の読める位置に" in chosen
    vision = _fake_vision([_OK])
    with tempfile.TemporaryDirectory() as tmp:
        _run(["--badge", "58", "--mode", "14", "--cues", "face,number", "--rough-count", "1", "--skip-canva", "--out", tmp],
             generate=_FakeGenerator(), vision=vision)
    inspection = next(call["text"] for call in vision.seen if "検査係" in call["text"])
    assert "番号や紋章は位置と字形を確認し" in inspection and "この絵の手がかりではない" not in inspection
    # 種別が「値と説明」の並びのレコード（バイナ）でも、プロンプトには文として入る
    _, binor, _ = _run(["--badge", "2B", "--mode", "4", "--dry-run"])
    assert "[{'value'" not in binor and "PortableHumanoid(TaleBeastType)（最終的な設計目標）" in binor


def test_natural_request_picks_the_scene_composition_only_for_one_figure_with_a_scene():
    def ask(text, images):
        return {"subjects": [{"character": "58", "mode": 14}], "layout": "single", "composition": "scene",
                "scene": CARDS, "extra": ""}

    asked = []
    code, out, _ = _run(["--natural", "イソヤがカードになってトランプの山に混ざっている", "--dry-run"],
                        ask=lambda text, images: asked.append(text) or ask(text, images))
    assert code == 0 and "[構図: 場面に溶け込む]" in out and "composition" in asked[0]

    def no_scene(text, images):
        return {**ask(text, images), "scene": ""}

    code, out, _ = _run(["--natural", "イソヤをカードに", "--dry-run"], ask=no_scene)
    assert code == 0 and "[構図: 場面に溶け込む]" not in out            # 場面が無ければ、見せる構図に戻す

    def joint(text, images):
        return {"subjects": [{"character": "85", "mode": 0}, {"character": "73", "mode": 3}], "layout": "single",
                "composition": "scene", "scene": "図書館"}

    code, out, _ = _run(["--natural", "ハッコとナトミが図書館に紛れている", "--dry-run"], ask=joint)
    assert code == 0 and "[構図: 場面に溶け込む]" not in out            # 合同は、まだ見せる構図だけ


def test_connector_arguments_carry_the_composition_and_cues():
    argv = tools.build_argv({"badge": "58", "mode": 14, "composition": "scene", "scene": CARDS,
                             "cues": ["face", "number"], "cue_note": "茶色の長い髪", "quick": False})
    assert argv == ["--badge=58", "--mode=14", "--composition=scene", f"--scene={CARDS}", "--cues=face,number",
                    "--cue-note=茶色の長い髪"]
    args = shape_pipeline.parse_args(argv)
    assert args.composition == "scene" and args.cue_list == ["face", "number"]
    # 既定の構図と、空の手がかりは引数に出さない（以前の実行の記録と同じ形）
    assert tools.build_argv({"badge": "58", "mode": 14, "composition": "showcase", "cues": [], "cue_note": ""}) == [
        "--badge=58", "--mode=14"]
    plan = tools.dry_run(argv)
    assert plan["status"] == "dry_run" and plan["subjects"][0]["cues"] == ["face", "number"]
    assert tools.dry_run(["--badge=58", "--mode=14", "--composition=scene"])["status"] == "error"


def test_connector_input_checks_the_scene_composition():
    try:
        from src.mcp_server.server import GenerateCharacterInput
    except ImportError:                                # mcp を入れていない環境では、入力モデルを読めない
        return
    ok = GenerateCharacterInput(badge="58", mode=14, composition="scene", scene=CARDS, cues=["face", "number"])
    assert ok.composition == "scene" and ok.cues == ["face", "number"]
    for bad in ({"scene": ""}, {"scene": CARDS, "layout": "sequence"}, {"scene": CARDS, "amount": 0.5},
                {"scene": CARDS, "cues": ["nose"]}):
        try:
            GenerateCharacterInput(badge="58", mode=14, composition="scene", **bad)
        except ValueError:
            continue
        raise AssertionError(f"受け付けてはいけない入力を受け付けた: {bad}")


if __name__ == "__main__":
    from _runner import run_module_tests

    run_module_tests(globals())
