"""入力画像（加筆の起点・赤入れ・構図参考・参考画像）と、その受け渡しの回帰テスト。

    python tests/test_input_images.py
    python -m pytest tests/

生成 API・Vision・GCS は呼ばない（GCS はメモリー上の偽物に差し替える）。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from PIL import Image  # noqa: E402

from src.mcp_server import output_sink, tools  # noqa: E402
from src.pipeline import input_images, shape_pipeline  # noqa: E402
from src.utils.provider import GenerationResult  # noqa: E402
from test_pipeline import _LIGHT, _OK, _SEVERE, _fake_vision, _run  # noqa: E402


def _image(width: int = 64, height: int = 64, fmt: str = "PNG", color=(200, 30, 30, 255)) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGBA", (width, height), color)
    (image if fmt == "PNG" else image.convert("RGB")).save(buffer, format=fmt)
    return buffer.getvalue()


class _Painter:
    """呼び出しを記録し、PIL で開ける PNG を書く（縦横比の照合には、開ける画像が要る）。"""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, prompt, *, references, out_dir, stem, aspect, count):
        self.calls.append({"prompt": prompt, "references": list(references), "stem": stem, "count": count})
        files = []
        for index in range(1, count + 1):
            path = Path(out_dir) / f"{stem}_{index:02d}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_image())
            files.append(path)
        return GenerationResult(files=files, model="fake-model", api_mode="fake")


def _kinds(references: list[dict]) -> list[str]:
    return [ref.get("kind") or ("official" if ref.get("official") else "rough") for ref in references]


def _resolve(items, files: dict[str, bytes] | None = None, **options):
    files = files or {}
    return input_images.resolve(items, locate=lambda raw: (_ for _ in ()).throw(AssertionError(raw)),
                                fetch=lambda key: files[key], **options)


def _refused(items, expected: str, files=None, **options) -> None:
    try:
        _resolve(items, files, **options)
    except ValueError as err:
        assert expected in str(err), err
    else:
        raise AssertionError(f"受け付けてしまった: {expected}")


class _Bucket:
    """メモリー上の GCS バケット。"""

    def __init__(self, store: dict[str, bytes] | None = None):
        self.store: dict[str, bytes] = dict(store or {})
        self.signed: list[dict] = []

    def blob(self, key):
        return _Blob(self, key)

    def get_blob(self, key):
        return _Blob(self, key) if key in self.store else None

    def list_blobs(self, prefix=None):
        return [_Blob(self, key) for key in self.store if key.startswith(prefix or "")]


class _Blob:
    def __init__(self, bucket: _Bucket, name: str):
        self.bucket, self.name = bucket, name

    @property
    def size(self):
        return len(self.bucket.store[self.name])

    def download_as_bytes(self):
        return self.bucket.store[self.name]

    def upload_from_filename(self, path):
        self.bucket.store[self.name] = Path(path).read_bytes()

    def generate_signed_url(self, **options):
        self.bucket.signed.append(options)
        return f"https://signed.example/{self.name}"


@contextlib.contextmanager
def _gcs(bucket: _Bucket):
    """GCS を偽物に差し替える。シンクは local に固定する（.env が drive でも、実際の Drive や GCS に触れない）。"""
    with patch.object(output_sink, "_bucket", lambda: bucket), \
            patch.dict(os.environ, {"GCS_PREFIX": "shapechange", "OUTPUT_SINK": "local"}):
        yield bucket


def test_roles_provenance_and_counts_are_checked_before_generation():
    files = {"base": _image(), "mark": _image(), "tall": _image(64, 80), "near": _image(101, 100),
             "square": _image(100, 100), "jpeg": _image(fmt="JPEG"), "gif": b"GIF89a" + b"\x00" * 32,
             "text": b"not an image at all"}
    own = {"provenance": "own_work"}
    ok = _resolve([{"object_key": "base", "role": "base", "provenance": "own_generated"},
                   {"object_key": "mark", "role": "markup", **own},
                   {"object_key": "jpeg", "role": "reference", "note": "床の  タイルの\n質感", **own}], files)
    assert [image["kind"] for image in ok] == ["base", "markup", "reference"]
    assert ok[2]["role"] == "参考画像（用途: 床の タイルの 質感）" and ok[2]["mime"] == "image/jpeg"
    assert ok[0]["path"] is None and ok[0]["stages"] == (3, 5) and ok[1]["stages"] == (3, 4)

    # 出所は毎回必須。第三者の素材は、値として用意していない
    _refused([{"object_key": "base", "role": "base"}], "provenance", files)
    _refused([{"object_key": "base", "role": "base", "provenance": "licensed_third_party"}], "provenance", files)
    _refused([{"object_key": "base", "role": "object", **own}], "role", files)
    _refused([{"object_key": "jpeg", "role": "reference", **own}], "note", files)
    _refused([{"object_key": "base", "path": "x.png", "role": "base", **own}], "どちらか一方", files)
    _refused([{"role": "base", **own}], "どちらか一方", files)
    # 枚数
    _refused([{"object_key": "base", "role": "base", **own}] * 2, "1 枚まで", files)
    _refused([{"object_key": "jpeg", "role": "reference", "note": "質感", **own}] * 4, "3 枚まで", files)
    # 赤入れは加筆の起点が要る。縦横比が 2% 以上違えば受け付けない
    _refused([{"object_key": "mark", "role": "markup", **own}], "加筆の起点", files)
    _refused([{"object_key": "base", "role": "base", **own}, {"object_key": "tall", "role": "markup", **own}],
             "縦横比", files)
    _resolve([{"object_key": "square", "role": "base", **own}, {"object_key": "near", "role": "markup", **own}], files)
    # 中身で形式を判定する
    _refused([{"object_key": "gif", "role": "base", **own}], "PNG・JPEG・WebP", files)
    _refused([{"object_key": "text", "role": "base", **own}], "PNG・JPEG・WebP", files)
    with patch.object(input_images, "MAX_BYTES", 16):
        _refused([{"object_key": "base", "role": "base", **own}], "MB まで", files)


def test_markup_is_compared_with_the_iterate_base_and_composition_loses_its_colours():
    with tempfile.TemporaryDirectory() as tmp:
        previous = Path(tmp) / "previous.png"
        previous.write_bytes(_image(64, 64))
        files = {"same": _image(128, 128), "wide": _image(128, 64), "guide": _image(color=(10, 200, 10, 128))}
        own = {"provenance": "own_work"}
        _resolve([{"object_key": "same", "role": "markup", **own}], files, iterate_base=previous)
        _refused([{"object_key": "wide", "role": "markup", **own}], "縦横比", files, iterate_base=previous)

        images = _resolve([{"object_key": "guide", "role": "composition", **own}], files)
        (record,) = input_images.materialize(images, Path(tmp))
        written = Path(tmp) / record["file"]
        assert record["file"] == "inputs/composition_01.png" and record["grayscale"] and images[0]["path"] == written
        with Image.open(written) as gray:
            assert gray.mode == "L" and gray.size == (64, 64)
            assert gray.getpixel((0, 0)) > 100          # 半透明の部分は白地に重なる（黒くならない）


def test_each_stage_gets_only_the_images_for_its_role():
    painter, vision = _Painter(), _fake_vision([_LIGHT, _SEVERE])
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "in"
        source.mkdir()
        for name, data in (("base.png", _image()), ("markup.png", _image()), ("pose.png", _image(80, 40)),
                           ("tiles.jpg", _image(fmt="JPEG"))):
            (source / name).write_bytes(data)
        spec = json.dumps([
            {"role": "reference", "path": str(source / "tiles.jpg"), "note": "床のタイルの質感", "provenance": "own_photo"},
            {"role": "composition", "path": str(source / "pose.png"), "provenance": "own_work"},
            {"role": "markup", "path": str(source / "markup.png"), "provenance": "own_work"},
            {"role": "base", "path": str(source / "base.png"), "provenance": "own_generated"},
        ], ensure_ascii=False)
        argv = ["--badge", "57", "--mode", "4", "--out", tmp, "--rough-count", "2", "--skip-canva", "--images", spec]

        code, out, _ = _run(argv + ["--dry-run"])
        assert code == 0 and [path.name for path in Path(tmp).iterdir()] == ["in"]        # 確認だけでは何も書かない
        assert "役割 composition / 出所 own_work" in out and "参照3（構図参考）" in out

        code, out, _ = _run(argv, generate=painter, vision=vision)
        assert code == 0, out
        (run_dir,) = [path for path in Path(tmp).iterdir() if path.name != "in"]
        meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
        inputs = sorted(path.name for path in (run_dir / "inputs").iterdir())
        final_prompt = (run_dir / "prompt.txt").read_text(encoding="utf-8")
        with Image.open(run_dir / "inputs" / "composition_01.png") as gray:
            assert gray.mode == "L"

    assert run_dir.name.endswith("_gemini_mode04_NumberTales-57_iter1") and meta["iteration"] == 1
    assert inputs == ["base_01.png", "composition_01.png", "markup_01.png", "reference_01.jpg"]
    assert [(item["role"], item["provenance"], item["stages"]) for item in meta["input_images"]] == [
        ("reference", "own_photo", [3, 5]), ("composition", "own_work", [3]),
        ("markup", "own_work", [3, 4]), ("base", "own_generated", [3, 5])]

    rough, fix, regen, synth = painter.calls
    # Stage 3: 加筆の起点 → 赤入れ → 構図参考 → 公式画像 → 参考画像
    kinds = _kinds(rough["references"])
    assert kinds[:3] == ["base", "markup", "composition"] and kinds[-1] == "reference" and "official" in kinds
    for expected in ("参照1（加筆の起点）からは構図と良い点を引き継ぎ、赤入れの指示だけを反映する",
                     "参照2（赤入れ）は、参照1（加筆の起点）に赤で指示を描き込んだもの",
                     "参照3（構図参考）からは、ポーズ・配置・画角だけを借りる",
                     "は「床のタイルの質感」のためだけに使う", "公式の参照画像と設定資料を採る"):
        assert expected in rough["prompt"], expected
    # Stage 4: ラフを起点にした修正には公式画像と赤入れだけ。作り直しはラフと同じ参照
    assert fix["references"][0]["role"] == "修正対象のラフ"
    assert set(_kinds(fix["references"][1:])) == {"official", "markup"}
    assert "- 参照1: 修正対象のラフ" in fix["prompt"] and "構図参考" not in fix["prompt"]
    assert "床のタイル" not in fix["prompt"] and "赤い印と指示の文字そのものは絵に描かない" in fix["prompt"]
    assert _kinds(regen["references"]) == kinds
    # Stage 5: ラフ → 加筆の起点 → 公式画像 → 参考画像（赤入れと構図参考は渡さない）
    kinds5 = _kinds(synth["references"])
    assert kinds5[:3] == ["rough", "rough", "base"] and kinds5[-1] == "reference"
    assert "markup" not in kinds5 and "composition" not in kinds5
    assert "- 参照1: 検査を通ったラフ" in synth["prompt"]
    assert "参照3（加筆の起点）からは構図と良い点を引き継ぐ。直した点は、検査を通ったラフに従う。" in synth["prompt"]
    assert "赤入れ" not in synth["prompt"] and "構図参考" not in synth["prompt"]
    assert synth["prompt"].endswith(final_prompt)
    # 検査は、赤入れが直ったか・印が残っていないかも見る
    inspection = next(call for call in vision.seen if "検査係" in call["text"])
    assert inspection["labels"][0] == "検査対象のラフ" and inspection["labels"][-1].startswith("赤入れ")
    assert "赤入れ未反映" in inspection["text"] and "赤入れの印" in inspection["text"]


def test_quick_iteration_is_one_edit_that_reuses_the_earlier_observation():
    def no_vision(text, images):
        raise AssertionError("単発の改稿では観察も検査も呼ばない")

    with tempfile.TemporaryDirectory() as tmp:
        base = ["--badge", "57", "--mode", "4", "--out", tmp, "--skip-canva"]
        code, out, _ = _run(base + ["--rough-count", "1"], generate=_Painter(), vision=_fake_vision([_OK]))
        assert code == 0, out
        (first,) = Path(tmp).iterdir()
        assert "尾は 7 本に見える" in json.loads((first / "run_meta.json").read_text(encoding="utf-8"))[
            "design_observations"]["NumberTales-57:humanoid"]
        (Path(tmp) / "marks").mkdir()
        (Path(tmp) / "marks" / "red.png").write_bytes(_image())
        spec = json.dumps([{"role": "markup", "path": str(Path(tmp) / "marks" / "red.png"), "provenance": "own_work"}])

        painter = _Painter()
        code, out, _ = _run(base + ["--quick", "--iterate-from", first.name, "--images", spec],
                            generate=painter, vision=no_vision)
        assert code == 0, out
        second = next(path for path in Path(tmp).iterdir() if path.name.endswith("_iter1"))
        meta = json.loads((second / "run_meta.json").read_text(encoding="utf-8"))
        painter2 = _Painter()
        _run(base + ["--quick", "--iterate-from", second.name, "--revisions", "表情を笑顔に"],
             generate=painter2, vision=no_vision)
        third = [path.name for path in Path(tmp).iterdir() if path.name.endswith("_iter2")]

    (call,) = painter.calls
    assert meta["pipeline"] == "quick" and meta["retry_of"] == first.name and meta["status"] == "ok"
    assert _kinds(call["references"])[:2] == ["base", "markup"]
    assert call["prompt"].startswith("[軽い加筆の依頼]") and "尾は 7 本に見える" in call["prompt"]
    assert "参照1（加筆の起点）からは構図と良い点を引き継ぎ、赤入れの指示だけを反映する。" in call["prompt"]
    assert meta["results"] == [{"file": "mode04_NumberTales-57_01.png", "status": "ok"}]
    # 単発の改稿が引き継いだ観察は、その次の改稿にも渡る
    assert len(third) == 1 and "尾は 7 本に見える" in painter2.calls[0]["prompt"]
    assert "参照1（加筆の起点）からは構図と良い点を引き継ぎ、[前回からの変更点] だけを反映する。" in painter2.calls[0]["prompt"]


def test_joint_run_uses_input_images_only_for_the_final_picture():
    painter, vision = _Painter(), _fake_vision([_OK, _OK])
    vision.measurements = [{"tail_tips_visible": 8, "figure_count": 1}, {"tail_tips_visible": 7, "figure_count": 1}]
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "in").mkdir()
        for name in ("base.png", "red.png"):
            (Path(tmp) / "in" / name).write_bytes(_image(96, 64))
        spec = json.dumps([{"role": "base", "path": str(Path(tmp) / "in" / "base.png"), "provenance": "own_generated"},
                           {"role": "markup", "path": str(Path(tmp) / "in" / "red.png"), "provenance": "own_work"}])
        code, out, _ = _run(["--joint", "85:0,73:3", "--rough-count", "1", "--out", tmp, "--skip-canva",
                             "--images", spec], generate=painter, vision=vision)
        assert code == 0, out
        code, _, err = _run(["--joint", "85:0,73:3", "--quick", "--dry-run"])
    rough_85, rough_73, final = painter.calls
    assert set(_kinds(rough_85["references"])) == {"official"} and set(_kinds(rough_73["references"])) == {"official"}
    assert _kinds(final["references"])[:4] == ["rough", "rough", "base", "markup"]
    assert "参照4（赤入れ）は、参照3（加筆の起点）に" in final["prompt"]
    assert "赤入れが反映されたかの検査は行いません" in out
    assert code == 2 and "加筆の起点" in err            # 合同の単発は、起点の編集のときだけ


def test_upload_slot_is_a_short_lived_put_url_under_this_servers_prefix():
    with _gcs(_Bucket()) as bucket:
        slot = tools.create_upload_slot("image/png")
        assert slot["status"] == "ok" and slot["object_key"].startswith("shapechange/uploads/")
        assert slot["object_key"].endswith(".png") and slot["upload_url"].startswith("https://signed.example/")
        assert slot["headers"] == {"Content-Type": "image/png", "x-goog-content-length-range": "0,10485760"}
        assert "x-goog-content-length-range: 0,10485760" in slot["upload_command"]
        (signed,) = bucket.signed
        assert signed["method"] == "PUT" and signed["content_type"] == "image/png"
        assert signed["expiration"].total_seconds() == 15 * 60
        assert signed["headers"] == {"x-goog-content-length-range": "0,10485760"}
        assert tools.create_upload_slot("image/png")["object_key"] != slot["object_key"]
        assert tools.create_upload_slot("image/gif")["status"] == "error"
    with patch.dict(os.environ, {"GCS_BUCKET": ""}):
        missing = tools.create_upload_slot("image/png")
    assert missing["status"] == "error" and "GCS_BUCKET" in missing["message"]


def test_objects_are_read_only_from_this_servers_prefix_and_within_the_size_limit():
    store = {"shapechange/uploads/20261120/a.png": _image(), "numbertales/uploads/20261120/b.png": _image(),
             "shapechange/uploads/20261120/huge.png": b"x" * 64}
    with _gcs(_Bucket(store)):
        assert output_sink.read_object("shapechange/uploads/20261120/a.png") == store["shapechange/uploads/20261120/a.png"]
        for key, expected in (("numbertales/uploads/20261120/b.png", "の下だけ"),
                              ("shapechange/../numbertales/uploads/20261120/b.png", "の下だけ"),
                              ("https://example.com/a.png", "の下だけ"),
                              ("shapechange/uploads/20261120/none.png", "見つかりません")):
            try:
                output_sink.read_object(key)
            except ValueError as err:
                assert expected in str(err), err
            else:
                raise AssertionError(key)
        try:
            output_sink.read_object("shapechange/uploads/20261120/huge.png", max_bytes=16)
        except ValueError as err:
            assert "大きすぎます" in str(err)
        else:
            raise AssertionError("大きさの上限を超えた画像を読んだ")


def test_connector_dry_run_returns_the_prompt_and_the_images_with_their_roles():
    key = "shapechange/uploads/20261120/pose.png"
    with tempfile.TemporaryDirectory() as tmp, _gcs(_Bucket({key: _image(80, 40)})), \
            contextlib.redirect_stdout(io.StringIO()):
        argv = tools.build_argv({"badge": "57", "mode": 4, "images": [
            {"object_key": key, "role": "composition", "note": "", "provenance": "own_work"}]})
        assert argv[-1].startswith("--images=[{") and '"note"' not in argv[-1]
        plan = tools.dry_run(argv + [f"--out={tmp}"])
        outside = tools.dry_run(tools.build_argv({"badge": "57", "mode": 4, "images": [
            {"object_key": "numbertales/x.png", "role": "base", "provenance": "own_work"}]}))
        refused = tools.dry_run(tools.build_argv({"name": "四春", "mode": 4}))
        assert not list(Path(tmp).iterdir())
    assert plan["status"] == "dry_run" and plan["pipeline"] == "five-stage" and plan["max_generated_images"] == 7
    (image,) = plan["input_images"]
    assert (image["role"], image["stages"], image["grayscale"], image["source"]) == (
        "composition", [3], True, {"object_key": key})
    assert "data" not in image and (image["width"], image["height"]) == (80, 40)
    (subject,) = plan["subjects"]
    assert subject["shape"] == "Mode 04 真球" and "参照1（構図参考）" in subject["prompt"]
    assert subject["references"][0] == {"role": "構図参考（グレースケール）", "source": key, "stages": "Stage 3"}
    assert outside["status"] == "error" and "の下だけ" in outside["message"]
    assert refused["status"] == "error" and "許可されていません" in refused["message"]


def test_iteration_survives_a_restart_by_restoring_the_run_from_gcs():
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as fresh, _gcs(_Bucket()) as bucket:
        code, out, _ = _run(["--badge", "57", "--mode", "4", "--out", tmp, "--rough-count", "1", "--skip-canva"],
                            generate=_Painter(), vision=_fake_vision([_OK]))
        assert code == 0, out
        (run_dir,) = Path(tmp).iterdir()
        stored = output_sink.store_run(str(run_dir))
        prefix = f"shapechange/runs/{run_dir.name}/"
        assert stored == {"stored": [prefix + "stage5_final/mode04_NumberTales-57_01.png", prefix + "run_meta.json"],
                          "note": ""}

        # サーバーが止まって outputs/ が空になったあと
        with patch.object(tools, "default_output_base", lambda: Path(fresh)):
            try:
                tools.iterate_argv(run_dir.name, ["顔を大きく"], {})          # 手元だけで動かす設定では GCS を見ない
            except ValueError:
                pass
            else:
                raise AssertionError("OUTPUT_SINK=local で GCS から戻した")
            with patch.object(tools, "_keeps_runs_remotely", lambda: True):
                (listed,) = tools.list_runs()
                assert listed["run_id"] == run_dir.name and listed["location"] == "gcs"
                argv = tools.iterate_argv(run_dir.name, ["顔を大きく"], {"quick": True, "count": 1})
                assert tools.list_runs()[0]["location"] == "local"
            restored = Path(fresh) / run_dir.name
            assert (restored / "run_meta.json").is_file()
            assert (restored / "stage5_final" / "mode04_NumberTales-57_01.png").read_bytes() == bucket.store[
                prefix + "stage5_final/mode04_NumberTales-57_01.png"]
            assert argv[-4:] == [f"--iterate-from={run_dir.name}", "--revisions=顔を大きく", "--quick", "--count=1"]
            # 戻した実行を起点に、観察を引き継いだ単発の改稿ができる
            painter = _Painter()
            with contextlib.redirect_stdout(io.StringIO()):
                summary = shape_pipeline.run_pipeline(_without_out(argv) + [f"--out={fresh}"], generate=painter)
        assert summary["status"] == "ok" and "尾は 7 本に見える" in painter.calls[0]["prompt"]
        assert _kinds(painter.calls[0]["references"])[0] == "base"


def _without_out(argv: list[str]) -> list[str]:
    return tools._without(argv, flags=(), options=("--out",))


def test_iteration_does_not_inherit_the_previous_images_changes_or_quick():
    with tempfile.TemporaryDirectory() as tmp, patch.object(tools, "default_output_base", lambda: Path(tmp)):
        run_dir = Path(tmp) / "earlier-run"
        run_dir.mkdir()
        (run_dir / "run_meta.json").write_text(json.dumps({"argv": [
            "--badge=57", "--mode=4", '--images=[{"object_key": "shapechange/uploads/x.png", "role": "base"}]',
            "--revisions=前回の変更", "--iterate-from", "older-run", "--quick", "--scene=--quick を含む場面"]}),
            encoding="utf-8")
        argv = tools.iterate_argv("earlier-run", [], {"images": [], "quick": False, "count": None})
    assert argv == ["--badge=57", "--mode=4", "--scene=--quick を含む場面", "--iterate-from=earlier-run"]


if __name__ == "__main__":
    from _runner import run_module_tests

    run_module_tests(globals())
