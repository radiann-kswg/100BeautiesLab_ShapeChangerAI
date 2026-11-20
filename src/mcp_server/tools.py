"""mcp_server/tools.py — MCP ツールの中身（MCP SDK に依存しない部分）。

ツールの入力をパイプラインの引数リストへ変換し、ジョブとして実行して、完成画像を出力シンクへ
公開する。引数リストは CLI と同じ検証を通るので、利用可否のゲートや値の範囲はここで再実装しない。
"""

from __future__ import annotations

import json
import threading
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
_CANVA_REFRESH_LOCK = threading.Lock()
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
        if key == "subjects":            # [{"id": "85", "mode": 0, "state": "…"}, ...] → --joint=85:0,73:3 --states=…;
            states = [" ".join(str(item.get("state") or "").replace(";", "、").split()) for item in value]
            argv += _option("states", ";".join(states) if any(states) else None)
            value = ",".join(f"{item['id']}:{item['mode']}" for item in value)
            key = "joint"
        elif key == "revisions" and isinstance(value, list):
            value = "; ".join(str(item) for item in value)
        elif key == "images":            # [{"object_key", "role", "note", "provenance"}, ...] → --images=<JSON>
            value = json.dumps([{k: v for k, v in item.items() if v not in (None, "")} for item in value],
                               ensure_ascii=False) if value else None
        argv += _option(key.replace("_", "-"), value)
    return argv


def _without(argv: list[str], flags: tuple[str, ...], options: tuple[str, ...]) -> list[str]:
    """引数リストから、指定のフラグと、値を取るオプション（``--x=値`` と ``--x 値`` の両方）を除く。"""
    kept: list[str] = []
    skip = False
    for argument in argv:
        if skip:
            skip = False
            continue
        name = argument.split("=", 1)[0]
        if name in options:
            skip = "=" not in argument
        elif name not in flags:
            kept.append(argument)
    return kept


def iterate_argv(run_id: str, revisions: list[str], overrides: dict[str, Any]) -> list[str]:
    """以前の実行と同じ指定に、改稿の起点と変更点を足した引数リストを作る。

    起点の実行が手元に無ければ（サーバーが止まって ``outputs/`` が消えたあと）、GCS から戻す。
    前回の入力画像・変更点・単発の指定は引き継がない（前回の起点や赤入れは、前回の絵に対するもの）。
    """
    name = Path(run_id).name
    meta_file = default_output_base() / name / "run_meta.json"
    if not meta_file.is_file() and not (_keeps_runs_remotely()
                                        and output_sink.restore_run(name, default_output_base())):
        raise ValueError(f"実行 {run_id} が見つかりません。shapechange_list_runs で確認してください。")
    previous = json.loads(meta_file.read_text(encoding="utf-8")).get("argv") or []
    if not previous:
        raise ValueError(f"実行 {run_id} には元の指定が記録されていないため、改稿の起点にできません。")
    previous = _without(previous, flags=("--quick", "--dry-run"),
                        options=("--images", "--iterate-from", "--revisions", "--retry-of"))
    return previous + build_argv({"iterate_from": name, "revisions": revisions, **overrides})


def dry_run(argv: list[str]) -> dict[str, Any]:
    """生成せずに、プロンプトと役割の付いた画像の一覧を返す（画像の生成は呼ばない）。"""
    try:
        return shape_pipeline.preview(argv)
    except shape_pipeline.PipelineInputError as err:
        return {"status": "error", "message": str(err)}


def create_upload_slot(content_type: str) -> dict[str, Any]:
    """入力画像 1 枚の置き場を作り、アップロードのしかたを添えて返す。"""
    slot = output_sink.create_upload_slot(content_type)
    if slot["note"]:
        return {"status": "error", "message": slot.pop("note"),
                "hint": "GCS を使わない環境では、画像を references/ か outputs/ に置いて CLI の --images で渡します。"}
    headers = " ".join(f'-H "{name}: {value}"' for name, value in slot["headers"].items())
    slot.pop("note")
    return {"status": "ok", **slot,
            "upload_command": f'curl -sS -X PUT {headers} --upload-file <画像ファイル> "<upload_url>"',
            "message": "upload_url へ headers を付けて PUT したあと、object_key を生成ツールの images に渡してください。"
                       "受け付けるのは、このパイプラインの生成結果と依頼者自身の作品だけです。画像は 7 日で消えます。"}


def _keeps_runs_remotely() -> bool:
    """実行の記録を GCS にも置く設定か（手元だけで動かす ``OUTPUT_SINK=local`` では GCS に触れない）。"""
    return output_sink.current_sink() != "local"


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
        summary = _publish(summary)
        if _keeps_runs_remotely():                     # 改稿の起点を、サーバーが止まったあとも使えるように残す
            summary["stored"] = output_sink.store_run(summary["run_dir"])
        return summary

    job = MANAGER.submit(kind, {"argv": argv}, work, job_id=job_id)
    return {"job_id": job.job_id, "status": job.status, "kind": kind,
            "message": "ジョブを登録しました。shapechange_job_status で進捗と完成画像のリンクを確認してください。"}


def job_status(job_id: str) -> dict[str, Any]:
    job = MANAGER.get(job_id)
    if job is None:
        return {"status": "not_found",
                "message": "この job_id はありません（サーバーの再起動でジョブは消えます）。shapechange_list_runs で実行結果を確認してください。"}
    return job.snapshot()


def _run_entry(run_id: str, meta: dict[str, Any], location: str) -> dict[str, Any]:
    return {
        "run_id": run_id, "status": meta.get("status"), "pipeline": meta.get("pipeline"),
        "created_at": meta.get("created_at"), "retry_of": meta.get("retry_of"),
        "subjects": [f"{s['character']['name_ja']} → "
                     + (s["shape"].get("label") or f"Mode {s['shape']['mode']:02d} {s['shape']['name_ja']}")
                     for s in meta.get("subjects") or []],
        "results": [item["file"] for item in meta.get("results") or []],
        "location": location,
    }


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    """実行を新しい順に返す。手元の outputs/ に加えて、GCS に残した実行（``location: gcs``）も並べる。

    GCS だけに残っている実行も、``shapechange_iterate`` の起点にできる（そのときに手元へ戻す）。
    """
    base = default_output_base()
    runs: dict[str, dict[str, Any]] = {}
    for folder in sorted((p for p in base.iterdir() if p.is_dir()), reverse=True) if base.is_dir() else []:
        meta_file = folder / "run_meta.json"
        if not meta_file.is_file():
            continue
        runs[folder.name] = _run_entry(folder.name, json.loads(meta_file.read_text(encoding="utf-8")), "local")
        if len(runs) >= limit:
            break
    if _keeps_runs_remotely():
        for meta in output_sink.list_stored_runs(limit):
            runs.setdefault(meta["run_id"], _run_entry(meta["run_id"], meta, "gcs"))
    return sorted(runs.values(), key=lambda run: run["run_id"], reverse=True)[:limit]


def run_logs(run_id: str) -> dict[str, Any]:
    """実行 1 件の記録（プロンプト・条件・検査ログ・中間画像）を返す。"""
    run_dir = default_output_base() / Path(run_id).name
    meta_file = run_dir / "run_meta.json"
    if not meta_file.is_file() and _keeps_runs_remotely():      # 手元に無ければ、GCS に残した記録と完成画像を戻す
        output_sink.restore_run(run_dir.name, default_output_base())
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
        with _CANVA_REFRESH_LOCK:
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
    characters = []
    for record in dataset.get_characters():
        if work_key and record.get("work_key") != work_key:
            continue
        entry = {"work": record.get("work_key"), "badge": dataset.full_badge(record),
                 "id": dataset.identity_label(record), "name": dataset.display_name(record)}
        if states := dataset.state_names(record):          # 名前で選べる状態（state に渡す）
            entry["states"] = states
        if '"Costume"' in json.dumps((record.get("data") or {}).get("AppearanceDetail") or []):
            costumes = dataset.costume_names(record)       # 名前で選べる衣装（costume に渡す）。通常衣装だけなら出さない
            if costumes not in ([], ["usual"]):
                entry["costumes"] = costumes
        characters.append(entry)
    return characters
