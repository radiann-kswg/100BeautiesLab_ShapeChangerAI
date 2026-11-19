"""mcp_server/tools.py — MCP ツールの中身（MCP SDK に依存しない部分）。

ツールの入力をパイプラインの引数リストへ変換し、ジョブとして実行して、完成画像を出力シンクへ
公開する。引数リストは CLI と同じ検証を通るので、利用可否のゲートや値の範囲はここで再実装しない。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Callable

from ..pipeline import shape_pipeline
from ..shape.modes import build_shape_spec
from ..shape.vrc_source import MODE_COUNT, load_shader_source
from ..utils import dataset
from ..utils.paths import default_output_base
from . import output_sink
from .jobs import MANAGER

DRIVE_LABEL_PREFIX = "shapechange_"       # Drive / GCS 上で形状変化の出力だと分かるようにする
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _option(name: str, value: Any) -> list[str]:
    """値を ``--name=value`` の 1 要素で渡す（値が ``--`` で始まっても別の引数として解釈されない）。"""
    if value is None or value == "" or value is False:
        return []
    return [f"--{name}"] if value is True else [f"--{name}={value}"]


def build_argv(params: dict[str, Any]) -> list[str]:
    """ツール入力（キーは CLI の引数名のアンダースコア表記）を引数リストにする。"""
    argv: list[str] = []
    for key, value in params.items():
        if key == "subjects":            # [{"id": "85", "mode": 0}, ...] → --joint=85:0,73:3
            value = ",".join(f"{item['id']}:{item['mode']}" for item in value)
            key = "joint"
        elif key == "revisions" and isinstance(value, list):
            value = "; ".join(str(item) for item in value)
        argv += _option(key.replace("_", "-"), value)
    return argv


def iterate_argv(run_id: str, revisions: list[str], overrides: dict[str, Any]) -> list[str]:
    """以前の実行と同じ指定に、改稿の起点と変更点を足した引数リストを作る。"""
    meta_file = default_output_base() / Path(run_id).name / "run_meta.json"
    if not meta_file.is_file():
        raise ValueError(f"実行 {run_id} が見つかりません。shapechange_list_runs で確認してください。")
    previous = json.loads(meta_file.read_text(encoding="utf-8")).get("argv") or []
    if not previous:
        raise ValueError(f"実行 {run_id} には元の指定が記録されていないため、改稿の起点にできません。")
    previous = [argument for argument in previous if argument != "--quick"]
    return previous + build_argv({"iterate_from": Path(run_id).name, "revisions": revisions, **overrides})


def _publish(summary: dict[str, Any]) -> dict[str, Any]:
    label = DRIVE_LABEL_PREFIX + summary["run_id"]
    summary["outputs"] = output_sink.publish(summary["finals"], run_label=label)
    summary["sink"] = output_sink.current_sink()
    return summary


def submit(kind: str, argv: list[str], *, pipeline: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    """生成ジョブを登録して、すぐに ``{"job_id", "status"}`` を返す。"""
    run = pipeline or shape_pipeline.run_pipeline
    job_id = uuid.uuid4().hex[:12]

    def on_stage(stage: str, paths: list[Path], run_dir: Path) -> None:
        published = output_sink.publish_intermediate(
            [str(path) for path in paths], run_label=f"{DRIVE_LABEL_PREFIX}{run_dir.name}_{stage}")
        MANAGER.update_partial_result(job_id, stage, {"count": len(paths), "intermediate": published}, str(run_dir))

    def work() -> dict[str, Any]:
        summary = run(argv, confirm=lambda warning: MANAGER.confirm_reference(job_id, warning), on_stage=on_stage)
        return _publish(summary)

    job = MANAGER.submit(kind, {"argv": argv}, work, job_id=job_id)
    return {"job_id": job.job_id, "status": job.status, "kind": kind,
            "message": "ジョブを登録しました。shapechange_job_status で進捗と完成画像のリンクを確認してください。"}


def job_status(job_id: str) -> dict[str, Any]:
    job = MANAGER.get(job_id)
    if job is None:
        return {"status": "not_found",
                "message": "この job_id はありません（サーバーの再起動でジョブは消えます）。shapechange_list_runs で実行結果を確認してください。"}
    return job.snapshot()


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    """outputs/ の実行フォルダーを新しい順に返す（サーバーを再起動しても残る記録）。"""
    base = default_output_base()
    runs: list[dict[str, Any]] = []
    for folder in sorted((p for p in base.iterdir() if p.is_dir()), reverse=True) if base.is_dir() else []:
        meta_file = folder / "run_meta.json"
        if not meta_file.is_file():
            continue
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        runs.append({
            "run_id": folder.name, "status": meta.get("status"), "pipeline": meta.get("pipeline"),
            "created_at": meta.get("created_at"), "retry_of": meta.get("retry_of"),
            "subjects": [f"{s['character']['name_ja']} → Mode {s['shape']['mode']:02d} {s['shape']['name_ja']}"
                         for s in meta.get("subjects") or []],
            "results": [item["file"] for item in meta.get("results") or []],
        })
        if len(runs) >= limit:
            break
    return runs


def run_logs(run_id: str) -> dict[str, Any]:
    """実行 1 件の記録（プロンプト・条件・検査ログ・中間画像）を返す。"""
    run_dir = default_output_base() / Path(run_id).name
    meta_file = run_dir / "run_meta.json"
    if not meta_file.is_file():
        return {"status": "not_found", "message": f"実行 {run_id} が見つかりません。"}
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    read = lambda path: path.read_text(encoding="utf-8") if path.is_file() else None  # noqa: E731
    intermediates = sorted(
        str(path) for stage in ("stage3_rough", "stage4_correct") for path in run_dir.rglob(f"{stage}/*")
        if path.suffix.lower() in _IMAGE_SUFFIXES)
    return {
        "run_id": run_dir.name,
        "meta": meta,
        "prompt": read(run_dir / "prompt.txt"),
        "notes": read(run_dir / "notes.md"),
        "observations": [json.loads(path.read_text(encoding="utf-8"))
                         for path in sorted(run_dir.rglob("stage2_reference/observation.json"))],
        "inspections": [json.loads(path.read_text(encoding="utf-8"))
                        for path in sorted(run_dir.rglob("stage4_correct/analysis_log.json"))],
        "intermediate": output_sink.publish_intermediate(
            intermediates, run_label=f"{DRIVE_LABEL_PREFIX}{run_dir.name}_logs"),
    }


def refresh_canva_token() -> dict[str, Any]:
    """Canva のアクセストークンを更新し、保存できた場所を返す（トークンの値は返さない）。"""
    from ..tools import refresh_canva_token as canva_token

    try:
        return canva_token.refresh_and_store()
    except canva_token.CanvaTokenError as err:
        return {"status": "error", "message": str(err)}


def list_modes() -> list[dict[str, Any]]:
    source = load_shader_source()
    modes = []
    for mode in range(MODE_COUNT):
        spec = build_shape_spec(mode, source)
        modes.append({"mode": mode, "name": spec["name_ja"], "silhouette": spec["silhouette_ja"],
                      "dimensions": spec["dimensions"], "limbs": spec["limbs"]["control"]})
    return modes


def list_characters(work: str | None = None) -> list[dict[str, Any]]:
    work_key = dataset.normalize_work_key(work)
    return [{"work": record.get("work_key"), "badge": dataset.full_badge(record),
             "id": dataset.identity_label(record), "name": dataset.display_name(record)}
            for record in dataset.get_characters() if not work_key or record.get("work_key") == work_key]
