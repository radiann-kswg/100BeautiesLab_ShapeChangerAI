"""MCP ツールの中身（src/mcp_server/tools.py）の回帰テスト。MCP SDK と生成 API は使わない。

    python tests/test_mcp_tools.py
    python -m pytest tests/
"""
from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.mcp_server import tools  # noqa: E402
from src.pipeline import shape_pipeline  # noqa: E402
from test_pipeline import _OK, _FakeGenerator, _fake_vision  # noqa: E402


@contextlib.contextmanager
def _outputs(tmp: str):
    """ツールが見る outputs/ を一時フォルダーへ差し替え、シンクは local に固定する。"""
    original, sink = tools.default_output_base, os.environ.get("OUTPUT_SINK")
    tools.default_output_base = lambda: Path(tmp)
    # 変数を消すだけでは足りない: パイプラインが .env を読み直し、OUTPUT_SINK=drive の環境では実際に Drive へ上がる
    os.environ["OUTPUT_SINK"] = "local"
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            yield
    finally:
        tools.default_output_base = original
        if sink is None:
            del os.environ["OUTPUT_SINK"]
        else:
            os.environ["OUTPUT_SINK"] = sink


def _wait(job_id: str, unfinished: tuple[str, ...] = ("pending", "running")) -> dict:
    for _ in range(200):
        snapshot = tools.job_status(job_id)
        if snapshot["status"] not in unfinished:
            return snapshot
        time.sleep(0.05)
    raise AssertionError("ジョブが終わりません")


def test_tool_input_becomes_cli_arguments_without_option_injection():
    argv = tools.build_argv({"badge": "57", "mode": 0, "scene": "--out=/etc", "count": 1, "quick": False,
                             "skip_canva": True, "rough_count": None, "work": None})
    assert argv == ["--badge=57", "--mode=0", "--scene=--out=/etc", "--count=1", "--skip-canva"]
    args = shape_pipeline.parse_args(argv)
    assert args.scene == "--out=/etc" and args.out is None and args.mode == 0      # 値は値のまま届く
    joint = tools.build_argv({"subjects": [{"id": "85", "mode": 0}, {"id": "73", "mode": 3}], "revisions": ["a", "b"]})
    assert joint == ["--joint=85:0,73:3", "--revisions=a; b"]


def test_connector_accepts_all_seven_supported_input_image_roles():
    from pydantic import ValidationError

    from src.mcp_server.server import GenerateJointInput

    images = [
        {"object_key": "base", "role": "base", "provenance": "own_generated"},
        {"object_key": "markup", "role": "markup", "provenance": "own_work"},
        {"object_key": "composition", "role": "composition", "provenance": "own_work"},
        *[{"object_key": f"reference-{index}", "role": "reference", "note": "質感",
           "provenance": "own_photo"} for index in range(3)],
        {"object_key": "object", "role": "object", "provenance": "own_work"},
    ]
    request = {"subjects": [{"id": "85", "mode": 0}, {"id": "73", "mode": 3}], "images": images}
    assert len(GenerateJointInput(**request).images) == 7
    try:
        GenerateJointInput(**{**request, "images": [*images, images[-1]]})
    except ValidationError:
        pass
    else:
        raise AssertionError("7 枚を超える入力画像を受け付けた")


def test_job_runs_the_pipeline_and_publishes_the_finals():
    with tempfile.TemporaryDirectory() as tmp, _outputs(tmp):
        def pipeline(argv, **hooks):
            return shape_pipeline.run_pipeline(argv + [f"--out={tmp}"], generate=_FakeGenerator(),
                                               vision=_fake_vision([_OK]), **hooks)

        submitted = tools.submit("generate_character", tools.build_argv(
            {"badge": "57", "mode": 4, "rough_count": 1, "skip_canva": True}), pipeline=pipeline)
        assert submitted["status"] in ("pending", "running") and len(submitted["job_id"]) == 12
        done = _wait(submitted["job_id"])
        assert done["status"] == "succeeded", done["error"]
        result = done["result"]
        assert result["status"] == "ok" and result["sink"] == "local"
        (output,) = result["outputs"]
        assert output["name"] == "mode04_NumberTales-57_01.png" and Path(output["local_path"]).is_file()
        assert set(done["partial_result"]["stage_summary"]) == {"stage3", "stage4"}       # 途中経過も見える

        (run,) = tools.list_runs()
        assert run["run_id"] == result["run_id"] and run["subjects"] == ["57(イズナ) → Mode 04 真球"]
        logs = tools.run_logs(run["run_id"])
        assert "[依頼]" in logs["prompt"] and logs["inspections"][0][0]["action"] == "passed"
        assert [Path(item["local_path"]).name for item in logs["intermediate"]] == ["rough_01.png"]

        # 改稿は、起点の実行と同じ指定に、起点と変更点を足す
        argv = tools.iterate_argv(run["run_id"], ["尾を 7 本に"], {"count": None, "skip_canva": True})
        assert argv[:2] == ["--badge=57", "--mode=4"]
        assert argv[-3:] == [f"--iterate-from={run['run_id']}", "--revisions=尾を 7 本に", "--skip-canva"]
        for bad in ("no-such-run", "../outside"):
            try:
                tools.iterate_argv(bad, ["x"], {})
            except ValueError:
                continue
            raise AssertionError(bad)
    assert tools.job_status("missing")["status"] == "not_found"


def test_refused_or_invalid_requests_fail_the_job_with_the_reason():
    with tempfile.TemporaryDirectory() as tmp, _outputs(tmp):
        submitted = tools.submit("generate_character", tools.build_argv({"name": "四春", "mode": 4}))
        done = _wait(submitted["job_id"])
    assert done["status"] == "failed" and "許可されていません" in done["error"]


def test_iterating_quick_run_removes_quick_flag():
    with tempfile.TemporaryDirectory() as tmp, _outputs(tmp):
        run_dir = Path(tmp) / "quick-run"
        run_dir.mkdir()
        (run_dir / "run_meta.json").write_text(
            json.dumps({"argv": ["--badge=57", "--mode=4", "--quick"]}), encoding="utf-8"
        )
        argv = tools.iterate_argv("quick-run", ["顔を大きく"], {})
    assert "--quick" not in argv
    assert argv[:2] == ["--badge=57", "--mode=4"]


def test_remote_names_keep_joint_character_directories():
    first = tools.output_sink._remote_name(
        Path("/run/char_NumberTales-85/stage3_rough/rough_01.png"), "run-stage3"
    )
    second = tools.output_sink._remote_name(
        Path("/run/char_NumberTales-73/stage3_rough/rough_01.png"), "run-stage3"
    )
    assert first != second
    assert "char_NumberTales-85" in first and "char_NumberTales-73" in second


class _FakeBlob:
    def __init__(self, name: str, created: datetime | None, data: bytes = b"image"):
        self.name, self.time_created, self.size, self.content_type, self._data = name, created, len(data), None, data

    def generate_signed_url(self, **_):
        return f"https://signed.example/{self.name}"

    def download_as_bytes(self):
        return self._data


@contextlib.contextmanager
def _fake_gcs(blobs: list[_FakeBlob]):
    """google.cloud.storage を偽物に差し替える（GCS は呼ばない）。"""
    class Client:
        def list_blobs(self, bucket, prefix=None):
            return [blob for blob in blobs if blob.name.startswith(prefix or "")]

        def bucket(self, name):
            def blob(key):
                (found,) = [item for item in blobs if item.name == key]     # 無いキーは例外になる
                return found
            return types.SimpleNamespace(blob=blob)

    storage = types.ModuleType("google.cloud.storage")
    storage.Client = Client
    cloud = types.ModuleType("google.cloud")
    cloud.storage = storage
    google = types.ModuleType("google")
    google.cloud = cloud
    with patch.dict(sys.modules, {"google": google, "google.cloud": cloud, "google.cloud.storage": storage}), \
            patch.dict(os.environ, {"GCS_BUCKET": "bucket", "GCS_PREFIX": "shapechange"}):
        yield


def test_gcs_logs_list_recent_images_under_the_prefix_newest_first():
    now = datetime.now(timezone.utc)
    blobs = [_FakeBlob("shapechange/run_a__rough_01.jpg", now - timedelta(days=2)),
             _FakeBlob("shapechange/run_b__rough_01.jpg", now - timedelta(hours=1)),
             _FakeBlob("shapechange/run_c__rough_01.jpg", now - timedelta(days=80)),      # 期間の外
             _FakeBlob("shapechange/run_b__notes.txt", now),                              # 画像ではない
             _FakeBlob("numbertales/run_x__rough_01.jpg", now)]                           # 別の用途の接頭辞
    with _fake_gcs(blobs):
        listed = tools.output_sink.list_gcs_logs()
        assert [item["object_key"] for item in listed["objects"]] == [
            "shapechange/run_b__rough_01.jpg", "shapechange/run_a__rough_01.jpg"]
        assert listed["count"] == 2 and listed["note"] == ""
        assert listed["objects"][0]["url"] == "https://signed.example/shapechange/run_b__rough_01.jpg"
        assert tools.output_sink.list_gcs_logs(limit=1)["count"] == 1
        del os.environ["GCS_BUCKET"]
        assert "GCS_BUCKET 未設定" in tools.output_sink.list_gcs_logs()["note"]


def test_empty_gcs_prefix_uses_the_safe_default_for_listing_and_fetching():
    now = datetime.now(timezone.utc)
    blobs = [_FakeBlob("shapechange/run_a__rough_01.jpg", now),
             _FakeBlob("numbertales/run_b__rough_01.jpg", now)]
    with _fake_gcs(blobs):
        for configured in ("", "/", "///"):
            with patch.dict(os.environ, {"GCS_PREFIX": configured}):
                assert tools.output_sink._gcs_prefix() == "shapechange"
                listed = tools.output_sink.list_gcs_logs()
                assert [item["object_key"] for item in listed["objects"]] == [
                    "shapechange/run_a__rough_01.jpg"]
                outside = tools.output_sink.fetch_gcs_image_b64("numbertales/run_b__rough_01.jpg")
                assert outside["content"] == "" and "shapechange/ の下" in outside["note"]


def test_gcs_image_comes_only_from_this_servers_prefix():
    blobs = [_FakeBlob("shapechange/run_a__rough_01.jpg", None, b"jpeg-bytes"),
             _FakeBlob("numbertales/other.jpg", None, b"other")]
    with _fake_gcs(blobs):
        image = tools.output_sink.fetch_gcs_image_b64("run_a__rough_01.jpg")          # ファイル名だけでも引ける
        assert base64.b64decode(image["content"]) == b"jpeg-bytes" and image["size"] == 10
        assert image["object_key"] == "shapechange/run_a__rough_01.jpg" and image["mime_type"] == "image/jpeg"
        outside = tools.output_sink.fetch_gcs_image_b64("numbertales/other.jpg")
        assert outside["content"] == "" and "shapechange/ の下" in outside["note"]
        missing = tools.output_sink.fetch_gcs_image_b64("shapechange/none.jpg")
        assert missing["content"] == "" and "取得に失敗" in missing["note"]


def test_observation_failure_waits_for_the_users_answer():
    from src.utils.provider import ProviderError

    with tempfile.TemporaryDirectory() as tmp, _outputs(tmp):
        generator = _FakeGenerator()

        def pipeline(argv, **hooks):
            return shape_pipeline.run_pipeline(
                argv + [f"--out={tmp}"], generate=generator,
                vision=_fake_vision([_OK], observation=ProviderError("vision down")), **hooks)

        job_id = tools.submit("generate_character", tools.build_argv(
            {"badge": "57", "mode": 4, "rough_count": 1, "skip_canva": True}), pipeline=pipeline)["job_id"]
        for _ in range(200):
            waiting = tools.job_status(job_id)
            if waiting["status"] == "awaiting_confirmation":
                break
            time.sleep(0.05)
        assert waiting["status"] == "awaiting_confirmation" and "観察に失敗" in waiting["confirmation"]["reason"]
        assert not generator.calls                                   # 回答が来るまで生成しない
        tools.MANAGER.answer_reference(job_id, waiting["confirmation"]["request_id"], False)
        done = _wait(job_id, ("pending", "running", "awaiting_confirmation"))
    assert done["status"] == "succeeded" and done["result"]["status"] == "cancelled" and not generator.calls


def test_concurrent_canva_refreshes_are_serialized():
    from src.tools import refresh_canva_token as canva_token

    active = maximum = 0
    active_lock = threading.Lock()

    def refresh_and_store():
        nonlocal active, maximum
        with active_lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.05)
        with active_lock:
            active -= 1
        return {"status": "ok"}

    with patch.object(canva_token, "refresh_and_store", side_effect=refresh_and_store):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: tools.refresh_canva_token(), range(2)))
    assert results == [{"status": "ok"}, {"status": "ok"}]
    assert maximum == 1


def test_lists_for_choosing_inputs():
    modes = tools.list_modes()
    assert len(modes) == 16 and modes[4]["name"] == "真球" and modes[14]["limbs"] == "merged"
    characters = tools.list_characters("NumberTales")
    assert {"work": "#Works_NumberTales", "badge": "NTS-57", "id": "57", "name": "57(イズナ)"} in characters
    assert all(item["work"] == "#Works_NumberTales" for item in characters)
    # 名前で選べる状態と衣装は、持っているキャラクターにだけ添える
    by_badge = {item["badge"]: item for item in tools.list_characters()}
    assert by_badge["NTS-35"]["costumes"] == ["miko", "usual"] and "states" not in by_badge["NTS-35"]
    assert by_badge["SAR-EZ6"]["states"] == ["beasted", "normal"]
    joint = tools.build_argv({"subjects": [{"id": "SCG-E", "mode": 4, "state": "afterChanged"},
                                           {"id": "SCG-S", "mode": 4, "state": ""}]})
    assert joint == ["--states=afterChanged;", "--joint=SCG-E:4,SCG-S:4"]


if __name__ == "__main__":
    from _runner import run_module_tests

    run_module_tests(globals())
