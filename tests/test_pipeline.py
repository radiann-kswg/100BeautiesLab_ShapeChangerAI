"""パイプライン全体（プロンプト・参照の制限・記録）の回帰テスト。

    python tests/test_pipeline.py
    python -m pytest tests/

生成 API も Vision も呼ばず、差し替えた関数で 5 段階の流れと記録の残り方を確認する。
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.pipeline import shape_pipeline  # noqa: E402
from src.shape.vrc_source import shapechange_root  # noqa: E402
from src.utils.provider import GenerationResult, ProviderError  # noqa: E402

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def _run(argv, **hooks):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = shape_pipeline.run(argv, **hooks)
        except SystemExit as exit_:  # argparse の使い方エラー
            code = exit_.code
    return code, out.getvalue(), err.getvalue()


def test_dry_run_prints_prompt_and_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        code, out, _ = _run(["--badge", "57", "--mode", "4", "--out", tmp, "--dry-run"])
        assert code == 0 and not list(Path(tmp).iterdir())
    for expected in ("57(イズナ)", "Mode 04 真球", "7 tails in total", "Arabic numeral '57'",
                     "参照1:", "変形前: 身長 152 cm", "約 106.4 cm"):
        assert expected in out, expected


def test_layouts_and_midway_change_the_composition():
    _, single, _ = _run(["--badge", "57", "--mode", "0", "--dry-run"])
    _, sequence, _ = _run(["--badge", "57", "--mode", "0", "--layout", "sequence", "--dry-run"])
    _, sheet, _ = _run(["--badge", "57", "--mode", "0", "--layout", "five-view", "--dry-run"])
    _, midway, _ = _run(["--badge", "57", "--mode", "0", "--amount", "0.5", "--dry-run"])
    assert "変形前」「途中" in sequence and "変形前」「途中" not in single
    assert "五方向図" in sheet and "五方向図" not in single
    assert "変形量 約 50%" in midway and "まだ完成形にはしない" in midway
    code, _, err = _run(["--badge", "57", "--mode", "0", "--amount", "0.5", "--layout", "sequence", "--dry-run"])
    assert code == 2 and "--amount" in err          # argparse の使い方エラー


def test_refused_character_stops_before_generation():
    called = []
    code, _, err = _run(["--name", "四春", "--mode", "4"], generate=lambda *a, **k: called.append(1))
    assert code == 2 and not called and "許可されていません" in err


def test_extra_references_are_limited_to_reviewed_locations():
    shader_file = next(shapechange_root().rglob("*.hlsl"))  # 検証用の画像は退避済み。置き場所だけで拒否する
    for bad in (str(shader_file), str(_ROOT / "README.md")):
        code, _, err = _run(["--badge", "57", "--mode", "4", "--ref", bad, "--dry-run"])
        assert code == 2 and "--ref" in err, bad
    # references/ の中は受け付ける（リポジトリを汚さないよう、置き場を一時フォルダーへ差し替える）
    original = shape_pipeline.references_dir
    with tempfile.TemporaryDirectory() as tmp:
        shape_pipeline.references_dir = lambda: Path(tmp)
        try:
            own = Path(tmp) / "own-sketch.png"
            own.write_bytes(_PNG)
            code, out, _ = _run(["--badge", "57", "--mode", "4", "--no-refs", "--ref", str(own), "--dry-run"])
            assert code == 0 and "手元の追加資料" in out
        finally:
            shape_pipeline.references_dir = original


def test_quick_run_leaves_three_logs_and_images():
    def fake(prompt, *, references, out_dir, stem, aspect, count):
        assert aspect == "3:2" and count == 2 and len(references) >= 1
        from src.utils.image_io import save_image_bytes
        files = [save_image_bytes(_JPEG, out_dir / f"{stem}_{i:02d}.png") for i in (1, 2)]
        return GenerationResult(files=files, model="fake-model", api_mode="fake")

    with tempfile.TemporaryDirectory() as tmp:
        code, _, _ = _run(["--badge", "57", "--mode", "8", "--layout", "sequence", "--count", "2", "--quick",
                           "--out", tmp, "--revisions", "顔を大きく; 尾を 7 本に", "--retry-of", "prev-run"],
                          generate=fake)
        assert code == 0
        (run_dir,) = Path(tmp).iterdir()
        assert "_gemini_mode08_NumberTales-57" in run_dir.name
        names = sorted(p.name for p in run_dir.iterdir())
        # JPEG 実体は拡張子を .jpg に直して保存する
        assert names == ["mode08_NumberTales-57_01.jpg", "mode08_NumberTales-57_02.jpg",
                         "notes.md", "prompt.txt", "run_meta.json"]
        meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
        assert meta["status"] == "ok" and meta["model"] == "fake-model" and meta["service"] == "gemini"
        (subject,) = meta["subjects"]
        assert subject["character"]["identity"] == "57" and subject["ai_training_gate"]["allowed"] is True
        assert subject["shape"]["mode"] == 8 and meta["revisions"] == ["顔を大きく", "尾を 7 本に"]
        assert meta["sources"]["shapechange"]["commit"] and meta["sources"]["creations_ai"]["commit"]
        assert all(ref["path"].startswith("data/") for ref in subject["reference_images"])
        assert meta["argv"][:4] == ["--badge", "57", "--mode", "8"]          # 改稿で同じ指定を再現するために残す
        assert "顔を大きく" in (run_dir / "prompt.txt").read_text(encoding="utf-8")
        notes = (run_dir / "notes.md").read_text(encoding="utf-8")
        assert "再生成元: prev-run" in notes and "再試行で見直す点" in notes


def test_failed_generation_keeps_prompt_and_reason():
    def failing(*args, **kwargs):
        raise ProviderError("quota exceeded")

    with tempfile.TemporaryDirectory() as tmp:
        code, _, err = _run(["--badge", "57", "--mode", "4", "--quick", "--out", tmp], generate=failing)
        assert code == 1 and "quota exceeded" in err
        (run_dir,) = Path(tmp).iterdir()
        meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
        assert meta["status"] == "failed" and meta["errors"] == ["quota exceeded"]
        assert (run_dir / "prompt.txt").stat().st_size > 0 and (run_dir / "notes.md").is_file()


# ── 5 段階 ────────────────────────────────────────────────────────────


class _FakeGenerator:
    """呼び出しを記録し、指定の枚数だけ PNG を書く。``fail_on`` を含むプロンプトでは失敗する。"""

    def __init__(self, fail_on: str = ""):
        self.calls: list[dict] = []
        self.fail_on = fail_on

    def __call__(self, prompt, *, references, out_dir, stem, aspect, count):
        self.calls.append({"prompt": prompt, "references": references, "stem": stem, "count": count})
        if self.fail_on and self.fail_on in prompt:
            raise ProviderError("generation failed")
        from src.utils.image_io import save_image_bytes
        files = [save_image_bytes(_PNG, Path(out_dir) / f"{stem}_{i:02d}.png") for i in range(1, count + 1)]
        return GenerationResult(files=files, model="fake-model", api_mode="fake")


def _fake_vision(inspections, observation=None):
    """Stage 2 の観察と、Stage 4 の検査（ラフの順に ``inspections`` を返す）を演じる。"""
    queue = list(inspections)
    seen: list[dict] = []

    def vision(text, images):
        seen.append({"text": text, "labels": [label for label, _ in images]})
        if "観察してください" in text:
            if isinstance(observation, Exception):
                raise observation
            return observation or {"observations": ["尾は 7 本に見える"], "unknowns": ["背面の模様"], "conflicts": []}
        if "見えているとおりに数えて" in text:      # 設定を知らせない計数。判定の直後に、同じラフについて呼ばれる
            return vision.measurements.pop(0)
        outcome = queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    vision.seen = seen
    vision.measurements = [{"tail_tips_visible": 7, "figure_count": 1}] * len(queue)
    return vision


_OK = {"shape_issues": [], "violations": [], "missing": [], "composition_issues": [], "overall_ok": True}
_LIGHT = {**_OK, "missing": ["尾: 設定は 7 本だが 5 本しか見えない"], "composition_issues": ["左端が見切れている"]}
_SEVERE = {**_OK, "shape_issues": ["輪郭が球でなく人型のまま"], "violations": ["蛇足: 羽", "文字: ラベル"]}


def test_five_stages_inspect_fix_and_finalize():
    generator, vision, notified = _FakeGenerator(), _fake_vision([_LIGHT, _OK, _SEVERE]), []
    with tempfile.TemporaryDirectory() as tmp:
        code, out, _ = _run(["--badge", "57", "--mode", "4", "--out", tmp], generate=generator, vision=vision,
                            on_stage=lambda stage, paths, run_dir: notified.append((stage, len(paths))))
        assert code == 0, out
        (run_dir,) = Path(tmp).iterdir()
        files = sorted(p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*") if p.is_file())
        assert files == [
            "notes.md", "prompt.txt", "run_meta.json",
            "stage2_reference/observation.json",
            "stage3_rough/rough_01.png", "stage3_rough/rough_02.png", "stage3_rough/rough_03.png",
            "stage4_correct/analysis_log.json", "stage4_correct/inspection_prompt.txt",
            "stage4_correct/rough_01_corrected_01.png", "stage4_correct/rough_03_regen_01.png",
            "stage5_final/mode04_NumberTales-57_01.png",
        ], files
        meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
        log = json.loads((run_dir / "stage4_correct" / "analysis_log.json").read_text(encoding="utf-8"))
        prompt = (run_dir / "prompt.txt").read_text(encoding="utf-8")
    assert notified == [("stage3", 3), ("stage4", 3)]
    assert meta["status"] == "ok" and meta["pipeline"] == "five-stage"
    assert meta["results"] == [{"file": "stage5_final/mode04_NumberTales-57_01.png", "status": "ok"}]
    assert meta["stages"]["stage4"][0]["actions"] == ["corrected", "passed", "regenerated"]
    assert [entry["action"] for entry in log] == ["corrected", "passed", "regenerated"]
    # 観察は補助としてプロンプトに入り、以降のステージへ渡る
    assert "[公式設定画の観察（補助）]" in prompt and "尾は 7 本に見える" in prompt
    rough_call, fix_call, regen_call, synth_call = generator.calls
    assert rough_call["count"] == 3 and "尾は 7 本に見える" in rough_call["prompt"]
    # 軽い指摘はそのラフを起点に直し、重い指摘（形状 + 違反が 3 件以上）は作り直す
    assert fix_call["references"][0]["role"] == "修正対象のラフ" and "5 本しか見えない" in fix_call["prompt"]
    assert all(ref["official"] for ref in regen_call["references"]) and "蛇足: 羽" in regen_call["prompt"]
    # 清書の参照は、問題の無かったラフが先頭
    assert [Path(ref["path"]).name for ref in synth_call["references"][:2]] == ["rough_02.png", "rough_01_corrected_01.png"]
    # 検査には形状の指定と公式画像を渡している
    inspection = next(call for call in vision.seen if "検査係" in call["text"])
    assert "全身が一つの真球になる" in inspection["text"] and inspection["labels"][0] == "検査対象のラフ"
    assert any("Canva" in note for note in meta["stages"]["stage5"]["notes"])   # 連携が無ければ省いたと記録する


def test_counted_measurements_are_checked_against_the_settings_in_code():
    # 判定が「問題なし」でも、設定を知らせずに数えた本数が設定と違えば指摘になる
    vision = _fake_vision([_OK, _OK, _OK])
    vision.measurements = [{"tail_tips_visible": 5, "figure_count": 1}, {"tail_tips_visible": 7, "figure_count": 2},
                           {"tail_tips_visible": 7, "figure_count": 1}]
    with tempfile.TemporaryDirectory() as tmp:
        _run(["--badge", "57", "--mode", "4", "--out", tmp, "--skip-canva"], generate=_FakeGenerator(), vision=vision)
        (run_dir,) = Path(tmp).iterdir()
        log = json.loads((run_dir / "stage4_correct" / "analysis_log.json").read_text(encoding="utf-8"))
    assert [entry["action"] for entry in log] == ["corrected", "corrected", "passed"]
    assert any("先端が見えているのは 5 本。設定は 7 本" in item for item in log[0]["missing"])
    assert any("2 体描かれている" in item for item in log[1]["composition_issues"])
    # 数える呼び出しには設定（本数）を渡さない
    blind = next(call for call in vision.seen if "見えているとおりに数えて" in call["text"])
    assert "7" not in blind["text"] and blind["labels"] == ["数える対象の画像"]


def test_observation_failure_needs_explicit_approval():
    failing = ProviderError("vision down")
    for answer, expected in ((None, "cancelled"), (False, "cancelled"), (True, "ok")):
        generator = _FakeGenerator()
        with tempfile.TemporaryDirectory() as tmp:
            code, _, _ = _run(["--badge", "57", "--mode", "4", "--out", tmp, "--rough-count", "1"],
                              generate=generator, vision=_fake_vision([_OK], observation=failing),
                              confirm=lambda warning: answer)
            (run_dir,) = Path(tmp).iterdir()
            meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
            state = json.loads((run_dir / "stage2_reference" / "observation.json").read_text(encoding="utf-8"))
        assert meta["status"] == expected, (answer, meta["status"])
        if expected == "cancelled":       # 無回答も拒否も、生成を 1 回も呼ばずに止まる
            assert code == 1 and not generator.calls and state["status"] == "cancelled"
        else:
            assert code == 0 and state["status"] == "fallback_approved" and meta["stages"]["stage2"][0]["fallback"]


def test_failed_inspection_and_failed_synthesis_are_recorded_not_hidden():
    generator = _FakeGenerator(fail_on="[清書の依頼]")
    vision = _fake_vision([ProviderError("vision down"), _OK])
    with tempfile.TemporaryDirectory() as tmp:
        code, _, _ = _run(["--badge", "57", "--mode", "4", "--out", tmp, "--rough-count", "2", "--skip-canva"],
                          generate=generator, vision=vision)
        (run_dir,) = Path(tmp).iterdir()
        meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
        log = json.loads((run_dir / "stage4_correct" / "analysis_log.json").read_text(encoding="utf-8"))
        assert (run_dir / "stage5_final" / "mode04_NumberTales-57_01.png").is_file()   # Stage 4 の画像で代用
    assert code == 0 and meta["status"] == "partial"
    assert log[0]["skipped"] is True and log[0]["action"] == "passed" and log[1]["skipped"] is False
    assert any("清書に失敗" in error for error in meta["errors"])


def test_joint_run_inspects_each_character_then_draws_them_together():
    generator, vision = _FakeGenerator(), _fake_vision([_OK, _OK, _OK, _OK])
    vision.measurements = [{"tail_tips_visible": 8, "figure_count": 1}] * 2 + [{"tail_tips_visible": 7, "figure_count": 1}] * 2
    with tempfile.TemporaryDirectory() as tmp:
        code, out, _ = _run(["--joint", "85:0,73:3", "--scene", "公園の芝生", "--out", tmp, "--skip-canva"],
                            generate=generator, vision=vision)
        assert code == 0, out
        (run_dir,) = Path(tmp).iterdir()
        assert run_dir.name.endswith("_gemini_joint_NumberTales-85_NumberTales-73")
        for index, slug in enumerate(("NumberTales-85", "NumberTales-73"), start=1):
            root = run_dir / f"char_{slug}_{index:02d}"
            assert (root / "stage3_rough" / "rough_02.png").is_file()   # 1 体あたり 2 枚
            assert (root / "stage4_correct" / "analysis_log.json").is_file()
        assert (run_dir / "stage5_final" / "joint_01.png").is_file()
        meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
        prompt = (run_dir / "prompt.txt").read_text(encoding="utf-8")
    assert [s["shape"]["mode"] for s in meta["subjects"]] == [0, 3] and meta["status"] == "ok"
    rough_85, rough_73, final = generator.calls
    assert "公園の芝生" not in rough_85["prompt"] and "立方体" in rough_85["prompt"] and "膨体" in rough_73["prompt"]
    assert [ref["role"] for ref in final["references"][:2]] == ["検査を通ったラフ: 85(ハッコ)", "検査を通ったラフ: 73(ナトミ)"]
    for expected in ("1 体目: 85(ハッコ)", "2 体目: 73(ナトミ)", "Mode 00 立方体", "Mode 03 膨体", "公園の芝生", "同じ縮尺"):
        assert expected in prompt, expected
    for bad in (["--joint", "85:0"], ["--joint", "85:0,73:3", "--layout", "sequence"], ["--joint", "85:0,73:x"]):
        code, _, _ = _run(bad + ["--dry-run"])
        assert code == 2, bad


def test_joint_run_keeps_same_slug_characters_in_separate_directories():
    generator, vision = _FakeGenerator(), _fake_vision([_OK, _OK])
    vision.measurements = [{}, {}]
    with tempfile.TemporaryDirectory() as tmp:
        code, out, _ = _run(["--joint", "SCG-W:4,SCG-AW:4", "--rough-count", "1",
                             "--out", tmp, "--skip-canva"], generate=generator, vision=vision)
        assert code == 0, out
        (run_dir,) = Path(tmp).iterdir()
        roots = [run_dir / f"char_SinisterChangingGirls-W_{index:02d}" for index in (1, 2)]
        for root in roots:
            assert (root / "stage2_reference" / "observation.json").is_file()
            assert (root / "stage3_rough" / "rough_01.png").is_file()
            assert (root / "stage4_correct" / "analysis_log.json").is_file()
        rough_references = generator.calls[-1]["references"][:2]
        assert all(ref["role"].startswith("検査を通ったラフ: ") for ref in rough_references)
        assert len({Path(ref["path"]).parent for ref in rough_references}) == 2
        assert (run_dir / "stage5_final" / "joint_01.png").is_file()


def test_joint_synthesis_failure_is_returned_without_using_individual_roughs():
    from src.pipeline import final_generator

    with tempfile.TemporaryDirectory() as tmp:
        rough = Path(tmp) / "rough.png"
        rough.write_bytes(_PNG)

        def failing(*args, **kwargs):
            raise ProviderError("generation failed")

        try:
            final_generator.generate_finals(
                [{"path": rough}], header="", prompt="", references=[],
                stage_dir=Path(tmp) / "stage5", stem="joint", aspect="3:2", count=1,
                generator=failing, skip_canva=True, title="joint", fallback_to_rough=False,
            )
        except ProviderError as err:
            assert str(err) == "generation failed"
        else:
            raise AssertionError("合同清書の失敗が呼び出し側へ返されなかった")
        assert not list((Path(tmp) / "stage5").glob("joint_*.png"))


def test_joint_run_fails_without_finals_when_the_synthesis_fails():
    generator, vision = _FakeGenerator(fail_on="[清書の依頼]"), _fake_vision([_OK, _OK, _OK, _OK])
    vision.measurements = [{"tail_tips_visible": 8, "figure_count": 1}] * 2 + [{"tail_tips_visible": 7, "figure_count": 1}] * 2
    with tempfile.TemporaryDirectory() as tmp:
        code, _, _ = _run(["--joint", "85:0,73:3", "--out", tmp, "--skip-canva"], generate=generator, vision=vision)
        (run_dir,) = Path(tmp).iterdir()
        meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
        leftovers = list((run_dir / "stage5_final").glob("joint_*"))
    assert code == 1 and meta["status"] == "failed" and meta["results"] == []      # 1 体ぶんのラフを完成画像にしない
    assert not leftovers


def test_canva_failure_falls_back_to_that_image_only():
    from unittest.mock import patch

    from src.canva import generate as canva
    from src.pipeline import final_generator

    def generate(prompt, *, references, out_dir, stem, aspect, count):
        files = [Path(out_dir) / f"{stem}_{index:02d}.png" for index in range(1, count + 1)]
        for path in files:
            path.write_bytes(_PNG)
        return GenerationResult(files=files, model="fake", api_mode="fake")

    with tempfile.TemporaryDirectory() as tmp:
        stage_dir = Path(tmp) / "stage5"
        def export(image, out_dir, title):
            if image.name.endswith("_02.png"):
                raise ProviderError("Canva unavailable")
            return [Path(out_dir) / "first_export.png"]

        with patch.object(canva, "access_token", return_value="token"), \
                patch.object(canva, "export_via_canva", side_effect=export):
            result = final_generator.generate_finals(
                [], header="", prompt="", references=[], stage_dir=stage_dir,
                stem="final", aspect="1:1", count=2, generator=generate,
                skip_canva=False, title="test",
            )
    assert [path.name for path in result["all"]] == ["first_export.png", "final_02.png"]
    assert [path.name for path in result["canva"]] == ["first_export.png"]


def test_iterate_starts_from_a_previous_result_and_records_the_link():
    with tempfile.TemporaryDirectory() as tmp:
        _run(["--badge", "57", "--mode", "4", "--quick", "--out", tmp], generate=_FakeGenerator())
        (first,) = Path(tmp).iterdir()
        generator = _FakeGenerator()
        code, _, _ = _run(["--badge", "57", "--mode", "4", "--out", tmp, "--rough-count", "1", "--skip-canva",
                           "--iterate-from", first.name, "--revisions", "尾を 7 本そろえる"],
                          generate=generator, vision=_fake_vision([_OK]))
        second = next(p for p in Path(tmp).iterdir() if p != first)
        meta = json.loads((second / "run_meta.json").read_text(encoding="utf-8"))
        outside = _run(["--badge", "57", "--mode", "4", "--out", tmp, "--iterate-from", str(_ROOT / "README.md"),
                        "--dry-run"])
    assert code == 0 and meta["retry_of"] == first.name
    rough_call = generator.calls[0]
    assert rough_call["references"][0]["role"] == "以前の生成結果（改稿の起点）"
    assert Path(rough_call["references"][0]["path"]).name == "mode04_NumberTales-57_01.png"
    assert "尾を 7 本そろえる" in rough_call["prompt"] and "構図と良い点を引き継ぎ" in rough_call["prompt"]
    assert outside[0] == 2                       # outputs/ の外の画像は起点にできない


def test_natural_request_is_mapped_to_arguments_and_reported():
    asked = []

    def ask(text, images):
        asked.append(text)
        return {"subjects": [{"character": "85", "mode": 0}], "layout": "single", "arms": None, "legs": "hide",
                "appendages": None, "scene": "図書館", "extra": ""}

    code, out, _ = _run(["--natural", "図書館で85(ハッコ)が箱化している様子", "--dry-run"], ask=ask)
    assert code == 0 and "依頼文の読み取り: 85(ハッコ) → Mode 00 立方体" in out
    assert "図書館" in out and "脚: 描かない。" in out
    assert "箱・箱化・キューブ・四角くなる → 0" in asked[0]       # 代表の形状の表を LLM に渡している

    def joint(text, images):
        return {"subjects": [{"character": "85", "mode": 0}, {"character": "ナトミ", "mode": 3}], "layout": "single"}

    code, out, _ = _run(["--natural", "ハッコを箱に、ナトミを風船に", "--dry-run"], ask=joint)
    assert code == 0 and "85(ハッコ) → Mode 00 立方体 / 73(ナトミ) → Mode 03 膨体" in out

    def unknown(text, images):
        return {"subjects": [{"character": "存在しない誰か", "mode": 0}]}

    code, _, err = _run(["--natural", "だれかを箱に", "--dry-run"], ask=unknown)
    assert code == 2 and "読み取れませんでした" in err

    # MCP ツールは作品を「--work=X」の 1 語で渡す。この形でも、読み取りの段階で作品を絞り込む
    code, _, err = _run(["--natural", "85 を箱に", "--work=SinisterChangingGirls", "--dry-run"], ask=ask)
    assert code == 2 and "自然文の依頼を読み取れませんでした" in err
    code, out, _ = _run(["--natural", "85 を箱に", "--work=NumberTales", "--dry-run"], ask=ask)
    assert code == 0 and "85(ハッコ) → Mode 00 立方体" in out


def test_canva_finishing_runs_only_with_this_repos_own_token():
    import os

    from src.pipeline import final_generator

    exported = []

    def fake_export(image, out_dir, title):
        exported.append((image.name, title))
        from src.utils.image_io import save_image_bytes
        return [save_image_bytes(_PNG, Path(out_dir) / f"{image.stem}_canva_01.png")]

    original = final_generator.canva.export_via_canva
    saved = {key: os.environ.pop(key, None) for key in ("SHAPECHANGE_CANVA_ACCESS_TOKEN", "CANVA_ACCESS_TOKEN")}
    final_generator.canva.export_via_canva = fake_export
    try:
        os.environ["CANVA_ACCESS_TOKEN"] = "generators-ai-token"      # GeneratorsAI の連携は使わない
        with tempfile.TemporaryDirectory() as tmp:
            _run(["--badge", "57", "--mode", "4", "--out", tmp, "--rough-count", "1"],
                 generate=_FakeGenerator(), vision=_fake_vision([_OK]))
        assert not exported
        os.environ["SHAPECHANGE_CANVA_ACCESS_TOKEN"] = "own-token"
        with tempfile.TemporaryDirectory() as tmp:
            _run(["--badge", "57", "--mode", "4", "--out", tmp, "--rough-count", "1"],
                 generate=_FakeGenerator(), vision=_fake_vision([_OK]))
            (run_dir,) = Path(tmp).iterdir()
            meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
        assert exported == [("mode04_NumberTales-57_01.png", "ShapeChange 57(イズナ) Mode 04")]
        assert meta["results"][0]["file"] == "stage5_final/canva/mode04_NumberTales-57_01_canva_01.png"
    finally:
        final_generator.canva.export_via_canva = original
        for key, value in saved.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value


if __name__ == "__main__":
    from _runner import run_module_tests

    run_module_tests(globals())
