"""utils/paths.py — リポジトリ内のパスと実行フォルダーの組み立て。

出力は AGENTS.md「出力と記録」の ``outputs/<run-id>/`` に置く（Git 追跡対象外）。
1 実行 = 1 フォルダーで、再生成は必ず別の実行フォルダーになる。
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path

_SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_output_base() -> Path:
    return project_root() / "outputs"


def references_dir() -> Path:
    return project_root() / "references"


def git_commit(repo: Path) -> str | None:
    """リポジトリ（サブモジュール）の HEAD コミット。取得できなければ None。"""
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (done.stdout.strip() or None) if done.returncode == 0 else None


def sanitize_token(value: object) -> str:
    """フォルダー名に使える ASCII トークンへ正規化する（"3/3" → "3-3"）。"""
    text = _SAFE_TOKEN_RE.sub("-", str(value).strip()).strip("-")
    return text or "x"


def build_run_dir(provider: str, label: str, base_dir: str | Path | None = None) -> Path:
    """``{base}/{YYYYMMDD_HHMMSS}_{provider}_{label}/`` を作って返す。

    ``label`` は ``mode04_NumberTales-57``（単体）や ``joint_NumberTales-85_NumberTales-73``（合同）。

    同じ秒に同じ条件で再実行しても既存フォルダーへ混ぜないよう、衝突時は連番を付ける。
    """
    base = Path(base_dir) if base_dir is not None else default_output_base()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{ts}_{sanitize_token(provider)}_{sanitize_token(label)}"
    folder = base / name
    serial = 2
    base.mkdir(parents=True, exist_ok=True)      # outputs/ は Git 追跡外なので、初回や新しいコンテナには無い
    while True:
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            folder = base / f"{name}_{serial}"
            serial += 1
