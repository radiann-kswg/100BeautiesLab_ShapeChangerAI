"""canva/generate.py — 完成画像を Canva のデザインにして書き出す（Stage 5 の仕上げ）。

Canva Connect API に画像生成の機能は無い。生成済みの画像をアップロードし、デザインを作って
書き出すだけで、絵そのものは変わらない。Canva 上に編集できるデザインが残る。
https://www.canva.dev/docs/connect/

環境変数 (.env):
    SHAPECHANGE_CANVA_ACCESS_TOKEN  このリポジトリ用の Canva 連携のアクセストークン
    CANVA_EXPORT_FORMAT             png / jpg（既定 png）

GeneratorsAI の ``CANVA_ACCESS_TOKEN`` は読まない。Canva のリフレッシュトークンは使うたびに
入れ替わるため、2 つのサーバーが同じ連携を共有すると片方が失効する。
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
from pathlib import Path

from ..utils.image_io import save_image_bytes
from ..utils.provider import ProviderError

_API_BASE = "https://api.canva.com/rest/v1"
TOKEN_ENV = "SHAPECHANGE_CANVA_ACCESS_TOKEN"


def access_token() -> str:
    return (os.environ.get(TOKEN_ENV) or "").strip()


def _request(method: str, url: str, token: str, *, body: bytes | None = None,
             headers: dict[str, str] | None = None) -> dict:
    request = urllib.request.Request(
        url, data=body, method=method, headers={"Authorization": f"Bearer {token}", **(headers or {})})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 — Canva API 固定
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def _poll_job(url: str, token: str, *, timeout: float = 120.0, interval: float = 2.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = _request("GET", url, token).get("job") or {}
        if job.get("status") == "success":
            return job
        if job.get("status") == "failed":
            raise ProviderError(f"Canva のジョブが失敗しました: {job.get('error')}")
        time.sleep(interval)
    raise ProviderError("Canva のジョブが時間内に終わりませんでした。")


def _job(response: dict, poll_url: str, token: str) -> dict:
    job = response.get("job") or {}
    if job.get("status") == "success":
        return job
    if not job.get("id"):
        raise ProviderError(f"Canva のジョブ ID を取得できません: {list(response)}")
    return _poll_job(f"{poll_url}/{job['id']}", token)


def export_via_canva(image: Path, out_dir: Path, title: str) -> list[Path]:
    """画像をアップロード → デザイン作成 → 書き出しし、保存したパスを返す。"""
    token = access_token()
    if not token:
        raise ProviderError(f"{TOKEN_ENV} が設定されていません。")
    fmt = (os.environ.get("CANVA_EXPORT_FORMAT") or "png").lower()
    try:
        name = base64.b64encode(image.name.encode("utf-8")).decode("ascii")
        upload = _request(
            "POST", f"{_API_BASE}/asset-uploads", token, body=image.read_bytes(),
            headers={"Content-Type": "application/octet-stream",
                     "Asset-Upload-Metadata": json.dumps({"name_base64": name})})
        asset_id = (_job(upload, f"{_API_BASE}/asset-uploads", token).get("asset") or {}).get("id")
        if not asset_id:
            raise ProviderError("Canva のアップロード結果に asset id がありません。")
        design = _request(
            "POST", f"{_API_BASE}/designs", token,
            body=json.dumps({"design_type": {"type": "preset", "name": "presentation"},
                             "asset_id": asset_id, "title": title}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        design_id = (design.get("design") or {}).get("id")
        if not design_id:
            raise ProviderError("Canva のデザイン作成結果に design id がありません。")
        export = _request(
            "POST", f"{_API_BASE}/exports", token,
            body=json.dumps({"design_id": design_id, "format": {"type": fmt}}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        urls = [url for url in _job(export, f"{_API_BASE}/exports", token).get("urls") or [] if url]
        saved: list[Path] = []
        for index, url in enumerate(urls, start=1):
            with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 — Canva の書き出し URL
                saved.append(save_image_bytes(response.read(), out_dir / f"{image.stem}_canva_{index:02d}.png"))
    except ProviderError:
        raise
    except Exception as err:  # noqa: BLE001 — HTTP・JSON の失敗をまとめて報告する
        raise ProviderError(f"Canva の処理に失敗しました: {type(err).__name__}: {err}") from err
    if not saved:
        raise ProviderError("Canva の書き出し結果が空でした。")
    return saved
