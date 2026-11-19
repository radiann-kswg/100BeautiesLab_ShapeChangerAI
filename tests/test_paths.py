"""実行フォルダーの作成に関する回帰テスト。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.utils.paths import build_run_dir


def test_build_run_dir_retries_when_creation_races():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        original_mkdir = Path.mkdir
        collided = False

        def create_then_report_collision(path, *args, **kwargs):
            nonlocal collided
            if path.parent == base and not collided:
                collided = True
                original_mkdir(path, *args, **kwargs)
                raise FileExistsError(path)
            return original_mkdir(path, *args, **kwargs)

        with patch.object(Path, "mkdir", create_then_report_collision):
            run_dir = build_run_dir("gemini", "mode04_NumberTales-57", base)

        assert collided and run_dir.name.endswith("_2")
        assert (base / run_dir.name.removesuffix("_2")).is_dir()
        assert run_dir.is_dir()


def test_build_run_dir_creates_the_output_base_when_it_is_missing():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = build_run_dir("gemini", "mode04_NumberTales-57", Path(tmp) / "outputs")
        assert run_dir.is_dir() and run_dir.parent.name == "outputs"


if __name__ == "__main__":
    from _runner import run_module_tests

    run_module_tests(globals())
