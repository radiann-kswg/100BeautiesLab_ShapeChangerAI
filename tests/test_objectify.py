"""物品化（--object: 家具や道具などの物への変形）の回帰テスト。

    python tests/test_objectify.py
    python -m pytest tests/

生成 API も Vision も呼ばず、差し替えた関数で、プロンプト・参照の渡り方・検査・記録を確認する。
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from PIL import Image  # noqa: E402

from src.mcp_server import tools  # noqa: E402
from src.pipeline import correction_generator, input_images  # noqa: E402
from src.shape.objects import build_object_spec, shape_label  # noqa: E402
from src.shape.vrc_source import ShapeSourceError  # noqa: E402
from test_pipeline import _OK, _FakeGenerator, _fake_vision, _run  # noqa: E402

ORCA = "シャチ型の浮き輪"


def _image(width: int = 64, height: int = 64) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (width, height), (20, 20, 20, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_object_replaces_the_mode_and_takes_only_a_single_finished_picture():
    code, out, _ = _run(["--badge", "57", "--object", ORCA, "--dry-run"])
    assert code == 0, out
    for expected in (f"57(イズナ) → 物品化「{ORCA}」", f"「{ORCA}」という物になった姿（物品化）",
                     "[物になっても維持する特徴（必須）]", f"[変形後の姿: 物品化「{ORCA}」]",
                     "人の体の輪郭（頭・胴・肩・腰の形）は残さない", "物の表面の配色と模様に移す",
                     "顔（目・口・表情）を、物の正面として自然な位置の表面に 1 つ描く",
                     "腕: 物の中へ収める", "耳と尾は元の形と本数のまま物の外側へ出し", "寸法の指定は無い",
                     "素材感はその物らしく描き"):
        assert expected in out, expected
    for absent in ("[変形後の形状: Mode", "[変形前後の体格の対比]", "寸法（変形前の身長を 1 とした比）", "変形先の参考"):
        assert absent not in out, absent

    # 顔・腕と脚・耳と尾の扱いは選べる
    _, plain, _ = _run(["--badge", "57", "--object", "花瓶", "--face", "none", "--arms", "hide",
                        "--appendages", "integrate", "--dry-run"])
    assert "顔は描かない。配色・模様・識別記号で" in plain and "腕: 描かない。" in plain
    assert "耳と尾は物の表面へ統合し" in plain and "- 顔立ちと瞳の色を変えない。" not in plain

    # 形状（--mode）とは排他。途中経過・五方向図・合同・大きさの指定には対応しない
    for extra, expected in ((["--mode", "4"], "どちらか一方"), (["--layout", "sequence"], "--layout single"),
                            (["--amount", "0.5"], "--layout single"), (["--size", "1.5"], "--layout single")):
        code, _, err = _run(["--badge", "57", "--object", ORCA, *extra, "--dry-run"])
        assert code == 2 and expected in err, (extra, err)
    code, _, err = _run(["--joint", "85:0,73:3", "--object", ORCA, "--dry-run"])
    assert code == 2 and "1 体だけ" in err
    code, _, err = _run(["--badge", "57", "--mode", "4", "--face", "none", "--dry-run"])
    assert code == 2 and "--face" in err
    code, _, err = _run(["--badge", "57", "--object", "花瓶", "--face", "none", "--cues", "face", "--dry-run"])
    assert code == 2 and "--face none と --cues face" in err
    code, _, err = _run(["--badge", "57", "--dry-run"])
    assert code == 2 and "--mode か --object" in err
    code, _, err = _run(["--badge", "57", "--object", "  ", "--dry-run"])
    assert code == 2 and "変形先の物の名前" in err


def test_object_spec_has_no_dimensions_and_checks_its_choices():
    spec = build_object_spec("  シャチ型の\n浮き輪 ", face="none", legs="keep")
    assert spec["kind"] == "object" and spec["name_ja"] == "シャチ型の 浮き輪" and spec["mode"] is None
    assert spec["dimensions"] is None and spec["face"] == {"rule": "object", "mode": "none"}
    assert spec["limbs"] == {"control": "selectable", "arms": "fold", "legs": "keep"} and spec["appendages"] == "keep"
    assert shape_label(spec) == "物品化「シャチ型の 浮き輪」"
    for bad in ({"name": ""}, {"name": "あ" * 61}, {"name": "花瓶", "face": "half"}, {"name": "花瓶", "arms": "wave"}):
        try:
            build_object_spec(bad.pop("name"), **bad)
        except ShapeSourceError:
            continue
        raise AssertionError(f"受け付けてしまった: {bad}")


def test_target_image_role_and_public_domain_sources_are_only_for_objects():
    files = {"orca": _image(), "tiles": _image()}

    def resolve(items):
        return input_images.resolve(items, locate=lambda raw: (_ for _ in ()).throw(AssertionError(raw)),
                                    fetch=lambda key: files[key])

    def refused(items, expected):
        try:
            resolve(items)
        except ValueError as err:
            assert expected in str(err), err
        else:
            raise AssertionError(f"受け付けてしまった: {expected}")

    credit = {"source_url": "https://example.org/orca-float", "license": "CC0  1.0"}
    (own,) = resolve([{"object_key": "orca", "role": "object", "provenance": "own_photo", **credit}])
    assert own["stages"] == (3, 4, 5) and own["role"].startswith("変形先の参考") and own["source_url"] == ""
    (free,) = resolve([{"object_key": "orca", "role": "object", "provenance": "cc0", **credit}])
    described = input_images.describe(free)
    assert described["license"] == "CC0 1.0" and described["source_url"] == credit["source_url"]
    assert "license" not in input_images.describe(own)
    # 出典の URL とライセンスの表記が要る。ほかの役割では、CC0 やパブリックドメインの素材も受け付けない
    refused([{"object_key": "orca", "role": "object", "provenance": "public_domain"}], "source_url")
    refused([{"object_key": "orca", "role": "object", "provenance": "cc0", "source_url": "ftp://x", "license": "CC0"}],
            "source_url")
    refused([{"object_key": "orca", "role": "object", "provenance": "cc0", "source_url": credit["source_url"]}], "license")
    refused([{"object_key": "tiles", "role": "reference", "note": "質感", "provenance": "cc0", **credit}], "役割 object")
    refused([{"object_key": "orca", "role": "object", "provenance": "licensed_third_party", **credit}], "provenance")
    refused([{"object_key": "orca", "role": "object", "provenance": "own_work"}] * 2, "1 枚まで")


def test_object_run_passes_the_target_image_to_every_stage_and_records_its_source():
    generator, vision = _FakeGenerator(), _fake_vision([_OK, _OK])
    vision.measurements = [{"tail_tips_visible": 7, "figure_count": 0}] * 2       # 顔の無い物は 0 体と数えられる
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "in"
        source.mkdir()
        (source / "orca.png").write_bytes(_image())
        spec = json.dumps([{"role": "object", "path": str(source / "orca.png"), "provenance": "public_domain",
                            "source_url": "https://example.org/orca-float", "license": "Public Domain Mark 1.0"}],
                          ensure_ascii=False)
        argv = ["--badge", "57", "--object", ORCA, "--face", "none", "--out", tmp, "--rough-count", "2",
                "--skip-canva", "--images", spec]

        # 役割 object の画像は、物品化のときだけ使える
        code, _, err = _run(["--badge", "57", "--mode", "4", "--out", tmp, "--images", spec, "--dry-run"])
        assert code == 2 and "--object（物品化）のときだけ" in err

        code, out, _ = _run(argv + ["--dry-run"])
        assert code == 0 and [path.name for path in Path(tmp).iterdir()] == ["in"]
        assert "（変形先の参考）は、変形先の物の形・構造・素材感の参考としてだけ使う" in out
        assert "ロゴ・文字・キャラクターの柄・配色は写さない" in out and "Public Domain Mark 1.0 — https://example.org" in out

        code, out, _ = _run(argv, generate=generator, vision=vision)
        assert code == 0, out
        (run_dir,) = [path for path in Path(tmp).iterdir() if path.name != "in"]
        meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
        log = json.loads((run_dir / "stage4_correct" / "analysis_log.json").read_text(encoding="utf-8"))
        inspection = (run_dir / "stage4_correct" / "inspection_prompt.txt").read_text(encoding="utf-8")
        notes = (run_dir / "notes.md").read_text(encoding="utf-8")
        assert (run_dir / "inputs" / "object_01.png").is_file()

    assert run_dir.name.endswith("_gemini_object_NumberTales-57") and meta["status"] == "ok"
    (subject,) = meta["subjects"]
    assert subject["shape"]["kind"] == "object" and subject["shape"]["label"] == f"物品化「{ORCA}」"
    assert subject["shape"]["dimensions"] is None and subject["shape"]["face"]["mode"] == "none"
    (recorded,) = meta["input_images"]
    assert recorded["role"] == "object" and recorded["provenance"] == "public_domain" and recorded["stages"] == [3, 4, 5]
    assert recorded["license"] == "Public Domain Mark 1.0" and recorded["source_url"] == "https://example.org/orca-float"
    assert f"物品化「{ORCA}」" in notes

    # ラフ（Stage 3）と清書（Stage 5）の両方に、変形先の参考が渡る
    rough_call, final_call = generator.calls[0], generator.calls[-1]
    assert [ref.get("kind") for ref in rough_call["references"]].count("object") == 1
    assert [ref.get("kind") for ref in final_call["references"]].count("object") == 1
    assert rough_call["references"][-1]["kind"] == "object"                 # 公式画像の後ろに添付する
    # 検査（Stage 4）: 物として読めるかと、表面へ移した手がかりを見る。参考画像も見せる
    for expected in (f"「{ORCA}」という物になった姿（物品化）", f"「{ORCA}」として一目で読めない",
                     "変形先の参考の画像と形や構造が大きく違う", "人の体の輪郭（頭・胴・肩・腰の形）が残りすぎている",
                     "「配色: 〜」", "顔が描かれていれば「顔: 描かない指定」", "「写し込み: 〜」"):
        assert expected in inspection, expected
    assert "寸法（変形前の身長を 1 とした比）" not in inspection
    judged = [entry for entry in vision.seen if "検査係" in entry["text"]]
    # ラフ 2 枚と、完成画像 1 枚（完成画像の検査にも参考画像を見せる）
    assert all(any(label.startswith("変形先の参考") for label in entry["labels"]) for entry in judged) and len(judged) == 3
    # 体数は照合しない（0 体と数えられても、指摘にしない）
    assert [entry["action"] for entry in log] == ["passed", "passed"]


def test_tail_count_is_checked_only_when_the_tail_keeps_its_form():
    character = {"tail_count": 7}
    measured = {"tail_tips_visible": 5, "figure_count": 2}
    for appendages, tails_reported in (("keep", True), ("integrate", False)):
        result = {"missing": [], "composition_issues": []}
        correction_generator._measured_issues(measured, character, result,
                                              build_object_spec("ソファ", appendages=appendages))
        assert bool(result["missing"]) is tails_reported and result["composition_issues"] == []
    result = {"missing": [], "composition_issues": []}
    correction_generator._measured_issues(measured, character, result)          # 形状の変形は、これまでどおり
    assert result["missing"] and result["composition_issues"]


def test_connector_arguments_carry_the_object_and_its_face():
    argv = tools.build_argv({"badge": "57", "mode": None, "object": ORCA, "face": "none", "quick": False,
                             "images": [{"object_key": "shapechange/uploads/20261120/a.png", "role": "object",
                                         "provenance": "cc0", "note": "", "license": "CC0 1.0",
                                         "source_url": "https://example.org/orca-float"}]})
    assert argv[:3] == ["--badge=57", f"--object={ORCA}", "--face=none"] and not any(a.startswith("--mode") for a in argv)
    (item,) = json.loads(argv[3].split("=", 1)[1])
    assert item == {"object_key": "shapechange/uploads/20261120/a.png", "role": "object", "provenance": "cc0",
                    "license": "CC0 1.0", "source_url": "https://example.org/orca-float"}
    plan = tools.dry_run(["--badge=57", f"--object={ORCA}"])
    assert plan["status"] == "dry_run" and plan["subjects"][0]["shape"] == f"物品化「{ORCA}」"
    assert tools.dry_run(["--badge=57", f"--object={ORCA}", "--mode=4"])["status"] == "error"
    entry = tools._run_entry("run", {"subjects": [{"character": {"name_ja": "57(イズナ)"},
                                                   "shape": {"label": f"物品化「{ORCA}」", "mode": None}}]}, "local")
    assert entry["subjects"] == [f"57(イズナ) → 物品化「{ORCA}」"]
    old = tools._run_entry("run", {"subjects": [{"character": {"name_ja": "57(イズナ)"},
                                                 "shape": {"mode": 4, "name_ja": "真球"}}]}, "local")
    assert old["subjects"] == ["57(イズナ) → Mode 04 真球"]                  # 以前の記録も読める


if __name__ == "__main__":
    from _runner import run_module_tests

    run_module_tests(globals())
