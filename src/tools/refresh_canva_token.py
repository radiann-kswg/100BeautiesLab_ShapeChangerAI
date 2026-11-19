"""tools/refresh_canva_token.py — ShapeChangerAI 用の Canva 連携のアクセストークンを取得・更新する。

GeneratorsAI の ``src/tools/refresh_canva_token.py`` と同じ方法（OAuth 2.0 の PKCE とリフレッシュトークン）。
連携は GeneratorsAI と分けてあり、環境変数は ``SHAPECHANGE_CANVA_*`` を使う。

    python -m src.tools.refresh_canva_token                        # 初回: ブラウザで許可して取得する
    python -m src.tools.refresh_canva_token --use-refresh-token    # 2 回目以降: ブラウザなしで更新する
    python -m src.tools.refresh_canva_token --push-secrets PROJECT # .env の 4 件を Secret Manager へ登録する

環境変数 (.env):
    SHAPECHANGE_CANVA_CLIENT_ID / SHAPECHANGE_CANVA_CLIENT_SECRET     Canva の連携（Developer Portal）の値
    SHAPECHANGE_CANVA_ACCESS_TOKEN / SHAPECHANGE_CANVA_REFRESH_TOKEN  このスクリプトが書き込む

Canva の連携には、Redirect URL に ``REDIRECT_URI``、スコープに ``SCOPES`` の 5 件を設定する。
リフレッシュトークンは 1 回使うと新しいものに入れ替わるので、更新したら必ず保存する。
トークンの値は表示も返却もしない。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from ..canva.generate import TOKEN_ENV as ACCESS_ENV
from ..utils.paths import project_root

REFRESH_ENV = "SHAPECHANGE_CANVA_REFRESH_TOKEN"
CLIENT_ID_ENV = "SHAPECHANGE_CANVA_CLIENT_ID"
CLIENT_SECRET_ENV = "SHAPECHANGE_CANVA_CLIENT_SECRET"
SECRET_NAMES = (ACCESS_ENV, REFRESH_ENV, CLIENT_ID_ENV, CLIENT_SECRET_ENV)

REDIRECT_URI = "http://127.0.0.1:3001/oauth/redirect"
AUTHORIZE_URL = "https://www.canva.com/api/oauth/authorize"
TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"
# canva/generate.py が行う操作（アップロード・デザイン作成・書き出し）に要るものだけ
SCOPES = ("asset:read", "asset:write", "design:content:read", "design:content:write", "design:meta:read")


class CanvaTokenError(RuntimeError):
    """Canva のトークンを取得・更新・保存できなかった。"""


def _require(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise CanvaTokenError(f"{name} が設定されていません。")
    return value


def _token_request(fields: dict[str, str]) -> dict[str, str]:
    """トークンのエンドポイントを呼び、新しいトークンを環境変数へ入れて、保存する 2 件を返す。"""
    basic = base64.b64encode(f"{_require(CLIENT_ID_ENV)}:{_require(CLIENT_SECRET_ENV)}".encode()).decode("ascii")
    request = urllib.request.Request(
        TOKEN_URL, data=urllib.parse.urlencode(fields).encode("ascii"), method="POST",
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 — Canva API 固定
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        try:
            code = str(json.loads(err.read().decode("utf-8")).get("error") or "")
        except Exception:  # noqa: BLE001 — 本文が JSON でなくても HTTP の番号は伝える
            code = ""
        hint = ("リフレッシュトークンが使用済みか失効しています。PC で `python -m src.tools.refresh_canva_token` を"
                "引数なしで実行して取り直してください。"
                if code == "invalid_grant" and fields.get("grant_type") == "refresh_token" else "")
        detail = f"HTTP {err.code} {code}".strip()
        raise CanvaTokenError(f"Canva がトークンを発行しませんでした（{detail}）。{hint}") from err
    except Exception as err:  # noqa: BLE001 — 通信・JSON の失敗をまとめて報告する
        raise CanvaTokenError(f"Canva のトークンのエンドポイントを呼べませんでした: {type(err).__name__}") from err
    if not data.get("access_token"):
        raise CanvaTokenError("Canva の応答にアクセストークンがありません。")
    tokens = {ACCESS_ENV: data["access_token"], REFRESH_ENV: data.get("refresh_token") or fields.get("refresh_token", "")}
    tokens = {name: value for name, value in tokens.items() if value}
    os.environ.update(tokens)
    return tokens


def refresh_access_token() -> dict[str, str]:
    """リフレッシュトークンでアクセストークンを更新する（ブラウザは使わない）。"""
    return _token_request({"grant_type": "refresh_token", "refresh_token": _require(REFRESH_ENV)})


def authorization_url(client_id: str, challenge: str, state: str) -> str:
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "code_challenge": challenge, "code_challenge_method": "S256", "scope": " ".join(SCOPES),
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT_URI, "state": state})


def _authorization_code(params: dict[str, str], state: str) -> str:
    """Canva から戻ってきたクエリを確かめて、認可コードを返す。"""
    if params.get("error"):
        raise CanvaTokenError(f"Canva で許可されませんでした（{params['error']}）。")
    if params.get("state") != state or not params.get("code"):
        raise CanvaTokenError("Canva からの戻りを確認できませんでした（state が一致しないか、コードがありません）。")
    return params["code"]


def _wait_for_redirect(timeout: int) -> dict[str, str]:
    """``REDIRECT_URI`` で Canva からの戻りを待ち、クエリを返す。"""
    redirect = urllib.parse.urlparse(REDIRECT_URI)
    received: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server の決まった名前
            url = urllib.parse.urlparse(self.path)
            if url.path != redirect.path:
                self.send_error(404)
                return
            received.update(urllib.parse.parse_qsl(url.query))
            body = "<p>Canva の許可を受け取りました。このタブを閉じて、ターミナルに戻ってください。</p>".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: Any) -> None:  # 認可コードを含む URL を表示しない
            pass

    deadline = time.time() + timeout
    try:
        server = http.server.HTTPServer((redirect.hostname, redirect.port), Handler)
    except OSError as err:
        raise CanvaTokenError(f"{redirect.netloc} で待ち受けられません（ほかのプログラムが使用中）: {err}") from err
    with server:
        server.timeout = 1
        while not received and time.time() < deadline:
            server.handle_request()
    if not received:
        raise CanvaTokenError(f"{timeout} 秒以内に Canva からの戻りがありませんでした。")
    return received


def authorize(timeout: int = 120) -> dict[str, str]:
    """ブラウザでの許可（PKCE）でトークンを取得する。"""
    client_id = _require(CLIENT_ID_ENV)
    _require(CLIENT_SECRET_ENV)
    verifier = secrets.token_urlsafe(96)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(32)
    print("次の URL をブラウザで開き、Canva で許可してください:\n")
    print(f"  {authorization_url(client_id, challenge, state)}\n")
    print(f"{timeout} 秒待ちます（戻り先: {REDIRECT_URI}）…")
    code = _authorization_code(_wait_for_redirect(timeout), state)
    return _token_request({"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                           "redirect_uri": REDIRECT_URI})


def update_env_file(path: Path, values: dict[str, str]) -> None:
    """.env の該当行を書き換える（無いキーは末尾に足す）。ほかの行と行末の種類は変えない。"""
    raw = path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.splitlines()
    seen: set[str] = set()
    for index, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in values:
            lines[index] = f"{key}={values[key]}"
            seen.add(key)
    lines += [f"{key}={value}" for key, value in values.items() if key not in seen]
    path.write_bytes((newline.join(lines) + newline).encode("utf-8"))


def _store_in_secret_manager(values: dict[str, str]) -> str:
    """Secret Manager に新しい版を足す。足せなかったときは理由を返す（足せたら空文字）。"""
    try:
        import google.auth  # type: ignore
        from google.cloud import secretmanager  # type: ignore
    except ImportError:
        return "google-cloud-secret-manager が入っていません"
    try:
        project = (os.environ.get("GOOGLE_CLOUD_PROJECT") or "").strip() or google.auth.default()[1]
        if not project:
            return "GCP のプロジェクトが分かりません"
        client = secretmanager.SecretManagerServiceClient()
        # リフレッシュトークンを先に保存する（こちらを失うと、ブラウザでの取り直しが必要になる）
        for name in sorted(values, key=lambda name: name != REFRESH_ENV):
            version = client.add_secret_version(request={"parent": f"projects/{project}/secrets/{name}",
                                                         "payload": {"data": values[name].encode("utf-8")}})
            _destroy_previous_version(client, version.name)
    except Exception as err:  # noqa: BLE001 — 資格情報・権限・シークレットの有無など
        return type(err).__name__
    return ""


def _destroy_previous_version(client: Any, new_version: str) -> None:
    """1 つ前の版を消す。入れ替わる前のトークンはもう使えず、版は残っているだけで課金されるため。"""
    secret, _, number = new_version.rpartition("/versions/")
    if number.isdigit() and int(number) > 1:
        try:
            client.destroy_secret_version(request={"name": f"{secret}/versions/{int(number) - 1}"})
        except Exception:  # noqa: BLE001 — 消す権限が無くても、新しい版の保存はできている
            pass


def refresh_and_store(env_file: Path | None = None) -> dict[str, Any]:
    """トークンを更新し、.env（あれば）と Secret Manager（使えれば）へ保存する。MCP サーバーから呼ぶ。"""
    tokens = refresh_access_token()
    env_file = env_file or project_root() / ".env"
    saved: list[str] = []
    env_file_note = ""
    if env_file.is_file():
        try:
            update_env_file(env_file, tokens)
            saved.append(".env")
        except (OSError, UnicodeError) as err:
            env_file_note = type(err).__name__
    reason = _store_in_secret_manager(tokens)
    if not reason:
        saved.append("Secret Manager")
    message = "Canva のアクセストークンを更新しました。このサーバーの以後の実行に使われます。"
    if saved:
        message += f"保存先: {' と '.join(saved)}。"
    else:
        message += ("保存はできませんでした。新しいリフレッシュトークンはこのサーバーのメモリーにしかなく、"
                    "サーバーが止まると失われます（その場合は PC で取り直して登録し直す）。")
    if env_file_note:
        message += f" .env への保存に失敗しました ({env_file_note})。"
    return {"status": "ok", "saved_to": saved, "secret_manager_note": reason,
            "env_file_note": env_file_note, "message": message}


def push_secrets(project: str, run: Callable[..., Any] = subprocess.run) -> None:
    """.env の 4 件を Secret Manager へ登録する（無ければ作り、有れば新しい版を足す）。gcloud を使う。"""
    gcloud = shutil.which("gcloud")
    if not gcloud:
        raise CanvaTokenError("gcloud が見つかりません。")
    values = {name: _require(name) for name in SECRET_NAMES}
    for name, value in values.items():
        exists = run([gcloud, "secrets", "describe", name, f"--project={project}"], capture_output=True).returncode == 0
        action = ["versions", "add", name] if exists else ["create", name, "--replication-policy=automatic"]
        # 値は標準入力で渡す（コマンドラインと画面に出さない）
        done = run([gcloud, "secrets", *action, "--data-file=-", f"--project={project}"],
                   input=value.encode("utf-8"), capture_output=True)
        if done.returncode:
            raise CanvaTokenError(f"{name} を Secret Manager（{project}）へ登録できませんでした。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.refresh_canva_token",
        description="ShapeChangerAI 用の Canva 連携のアクセストークンを取得・更新し、.env に書き込む。")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--use-refresh-token", action="store_true",
                      help="リフレッシュトークンで更新する（ブラウザなし。2 回目以降）")
    mode.add_argument("--push-secrets", metavar="PROJECT",
                      help=".env の SHAPECHANGE_CANVA_* 4 件を Secret Manager へ登録する（トークンは取得しない）")
    parser.add_argument("--timeout", type=int, default=120, help="ブラウザでの許可を待つ秒数（既定 120）")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    from ..pipeline.shape_pipeline import _load_env
    _load_env()
    env_file = project_root() / ".env"
    try:
        if args.push_secrets:
            push_secrets(args.push_secrets)
            print(f"[OK] {', '.join(SECRET_NAMES)} を Secret Manager（{args.push_secrets}）へ登録しました。")
            return 0
        if not env_file.is_file():      # 更新したトークンを保存できないまま、リフレッシュトークンを使わない
            raise CanvaTokenError(f"{env_file} がありません。.env.example を写して、Canva の連携の値を入れてください。")
        tokens = refresh_access_token() if args.use_refresh_token else authorize(args.timeout)
        update_env_file(env_file, tokens)
    except CanvaTokenError as err:
        print(f"[ERROR] {err}", file=sys.stderr)
        return 1
    print(f"[OK] .env の {' と '.join(tokens)} を更新しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
