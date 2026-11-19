"""Canva 連携のトークン取得・更新（src/tools/refresh_canva_token.py）の回帰テスト。Canva と GCP は呼ばない。

    python tests/test_canva_token.py
    python -m pytest tests/
"""
from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import types
import urllib.error
import urllib.parse
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.mcp_server import tools  # noqa: E402
from src.tools import refresh_canva_token as canva_token  # noqa: E402
from src.utils.provider import ProviderError  # noqa: E402

_KEYS = (*canva_token.SECRET_NAMES, "GOOGLE_CLOUD_PROJECT")
_CLIENT = {"SHAPECHANGE_CANVA_CLIENT_ID": "client-id", "SHAPECHANGE_CANVA_CLIENT_SECRET": "client-secret"}
_TOKENS = {"SHAPECHANGE_CANVA_ACCESS_TOKEN": "new-access", "SHAPECHANGE_CANVA_REFRESH_TOKEN": "new-refresh"}


@contextlib.contextmanager
def _env(**values):
    """テスト中だけ Canva 連携の環境変数を差し替える。"""
    saved = {key: os.environ.pop(key, None) for key in _KEYS}
    os.environ.update(values)
    try:
        yield
    finally:
        for key in _KEYS:
            os.environ.pop(key, None)
        os.environ.update({key: value for key, value in saved.items() if value is not None})


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _canva(calls: list, payload: dict | None = None, status: int = 200):
    """トークンのエンドポイントの偽物。受け取ったリクエストを calls に残す。"""
    body = json.dumps(payload or {"access_token": "new-access", "refresh_token": "new-refresh",
                                  "expires_in": 14400}).encode("utf-8")

    def urlopen(request, timeout=None):
        calls.append(request)
        if status != 200:
            raise urllib.error.HTTPError(request.full_url, status, "error", {}, io.BytesIO(body))
        return _Response(body)

    return patch.object(canva_token.urllib.request, "urlopen", urlopen)


def _fails(call, *args, error=canva_token.CanvaTokenError) -> str:
    try:
        call(*args)
    except error as err:
        return str(err)
    raise AssertionError(f"{call.__name__}{args} が失敗しませんでした")


def test_refresh_sends_the_refresh_grant_and_keeps_the_rotated_token():
    calls: list = []
    with _env(**_CLIENT, SHAPECHANGE_CANVA_REFRESH_TOKEN="old-refresh"), _canva(calls):
        tokens = canva_token.refresh_access_token()
        assert {key: os.environ[key] for key in _TOKENS} == _TOKENS      # 使ったリフレッシュトークンは次に使えない
    (request,) = calls
    assert request.full_url == canva_token.TOKEN_URL and request.get_method() == "POST"
    assert request.get_header("Authorization") == "Basic " + base64.b64encode(b"client-id:client-secret").decode()
    assert urllib.parse.parse_qs(request.data.decode()) == {"grant_type": ["refresh_token"],
                                                           "refresh_token": ["old-refresh"]}
    assert tokens == _TOKENS


def test_missing_settings_stop_before_calling_canva():
    calls: list = []
    for values in ({}, _CLIENT, {"SHAPECHANGE_CANVA_REFRESH_TOKEN": "old-refresh"}):
        with _env(**values), _canva(calls):
            assert "が設定されていません" in _fails(canva_token.refresh_access_token)
    assert not calls


def test_rejected_refresh_reports_the_error_code_and_keeps_the_environment():
    rejected = {"error": "invalid_grant", "error_description": "details from Canva"}
    with _env(**_CLIENT, SHAPECHANGE_CANVA_REFRESH_TOKEN="used"), _canva([], rejected, status=400):
        message = _fails(canva_token.refresh_access_token)
        assert os.environ["SHAPECHANGE_CANVA_REFRESH_TOKEN"] == "used"
        assert "SHAPECHANGE_CANVA_ACCESS_TOKEN" not in os.environ
    assert "HTTP 400 invalid_grant" in message and "取り直して" in message and "details from Canva" not in message


def test_authorization_request_and_the_checks_on_the_redirect():
    url = urllib.parse.urlparse(canva_token.authorization_url("client-id", "challenge", "state-1"))
    assert url._replace(query="").geturl() == "https://www.canva.com/api/oauth/authorize"
    assert dict(urllib.parse.parse_qsl(url.query)) == {
        "code_challenge": "challenge", "code_challenge_method": "S256", "response_type": "code",
        "client_id": "client-id", "redirect_uri": "http://127.0.0.1:3001/oauth/redirect", "state": "state-1",
        "scope": "asset:read asset:write design:content:read design:content:write design:meta:read"}
    assert canva_token._authorization_code({"code": "abc", "state": "state-1"}, "state-1") == "abc"
    for params in ({"code": "abc", "state": "other"}, {"state": "state-1"},
                   {"error": "access_denied", "state": "state-1"}):
        _fails(canva_token._authorization_code, params, "state-1")


def test_env_file_update_touches_only_the_given_keys():
    with tempfile.TemporaryDirectory() as tmp:
        env_file = Path(tmp) / ".env"
        env_file.write_bytes("GEMINI_API_KEY=keep\r\n# SHAPECHANGE_CANVA_ACCESS_TOKEN=説明\r\n"
                             "SHAPECHANGE_CANVA_ACCESS_TOKEN=old\r\n".encode("utf-8"))
        canva_token.update_env_file(env_file, {"SHAPECHANGE_CANVA_ACCESS_TOKEN": "a=b",
                                               "SHAPECHANGE_CANVA_REFRESH_TOKEN": "r"})
        assert env_file.read_bytes().decode("utf-8") == (
            "GEMINI_API_KEY=keep\r\n# SHAPECHANGE_CANVA_ACCESS_TOKEN=説明\r\n"
            "SHAPECHANGE_CANVA_ACCESS_TOKEN=a=b\r\nSHAPECHANGE_CANVA_REFRESH_TOKEN=r\r\n")


def test_server_refresh_saves_the_tokens_and_never_returns_them():
    with tempfile.TemporaryDirectory() as tmp, _canva([]), \
            _env(**_CLIENT, SHAPECHANGE_CANVA_REFRESH_TOKEN="old-refresh"), \
            patch.object(canva_token, "_store_in_secret_manager", return_value="PermissionDenied"):
        env_file = Path(tmp) / ".env"
        env_file.write_text("SHAPECHANGE_CANVA_REFRESH_TOKEN=old-refresh\n", encoding="utf-8")
        saved = canva_token.refresh_and_store(env_file)
        assert "SHAPECHANGE_CANVA_REFRESH_TOKEN=new-refresh" in env_file.read_text(encoding="utf-8")
        nowhere = canva_token.refresh_and_store(Path(tmp) / "no-such.env")       # Cloud Run には .env が無い
    assert saved["status"] == "ok" and saved["saved_to"] == [".env"]
    assert saved["secret_manager_note"] == "PermissionDenied"
    assert nowhere["saved_to"] == [] and "失われます" in nowhere["message"]
    assert not any(token in json.dumps([saved, nowhere]) for token in _TOKENS.values())


def test_secret_manager_gets_the_refresh_token_first():
    added: list = []
    destroyed: list = []

    class Client:
        def add_secret_version(self, request):
            added.append((request["parent"], request["payload"]["data"]))
            return types.SimpleNamespace(name=f"{request['parent']}/versions/{len(added) + 1}")

        def destroy_secret_version(self, request):
            destroyed.append(request["name"])
            if len(destroyed) == 2:
                raise PermissionError("destroy は許可されていない")

    secretmanager = types.ModuleType("google.cloud.secretmanager")
    secretmanager.SecretManagerServiceClient = Client
    cloud = types.ModuleType("google.cloud")
    cloud.secretmanager = secretmanager
    auth = types.ModuleType("google.auth")
    auth.default = lambda: (None, "project-from-credentials")
    google = types.ModuleType("google")
    google.auth, google.cloud = auth, cloud
    fakes = {"google": google, "google.auth": auth, "google.cloud": cloud,
             "google.cloud.secretmanager": secretmanager}
    with patch.dict(sys.modules, fakes), _env():
        assert canva_token._store_in_secret_manager(dict(_TOKENS)) == ""
    assert added == [("projects/project-from-credentials/secrets/SHAPECHANGE_CANVA_REFRESH_TOKEN", b"new-refresh"),
                     ("projects/project-from-credentials/secrets/SHAPECHANGE_CANVA_ACCESS_TOKEN", b"new-access")]
    # 入れ替わる前の版は消す（残すだけで課金される）。消せなくても、保存は成功として扱う
    assert destroyed == ["projects/project-from-credentials/secrets/SHAPECHANGE_CANVA_REFRESH_TOKEN/versions/1",
                         "projects/project-from-credentials/secrets/SHAPECHANGE_CANVA_ACCESS_TOKEN/versions/2"]

    auth.default = lambda: (_ for _ in ()).throw(RuntimeError("no credentials"))
    with patch.dict(sys.modules, fakes), _env():
        assert canva_token._store_in_secret_manager(dict(_TOKENS)) == "RuntimeError"    # 落とさずに理由を返す


def test_env_file_save_failure_does_not_skip_secret_manager():
    with tempfile.TemporaryDirectory() as tmp, _canva([]), \
            _env(**_CLIENT, SHAPECHANGE_CANVA_REFRESH_TOKEN="old-refresh"), \
            patch.object(canva_token, "update_env_file", side_effect=OSError), \
            patch.object(canva_token, "_store_in_secret_manager", return_value="") as store:
        env_file = Path(tmp) / ".env"
        env_file.write_text("SHAPECHANGE_CANVA_REFRESH_TOKEN=old-refresh\n", encoding="utf-8")
        result = canva_token.refresh_and_store(env_file)
    store.assert_called_once_with(_TOKENS)
    assert result["saved_to"] == ["Secret Manager"]
    assert result["env_file_note"] == "OSError"
    assert ".env への保存に失敗しました" in result["message"]


def test_push_secrets_creates_missing_secrets_and_passes_values_on_stdin():
    commands: list = []

    def run(command, **kwargs):
        commands.append((command[1:], kwargs.get("input")))
        missing = command[2] == "describe" and command[3] != "SHAPECHANGE_CANVA_CLIENT_ID"
        return types.SimpleNamespace(returncode=1 if missing else 0)

    values = {**_CLIENT, "SHAPECHANGE_CANVA_ACCESS_TOKEN": "access", "SHAPECHANGE_CANVA_REFRESH_TOKEN": "refresh"}
    with patch.object(canva_token.shutil, "which", return_value="gcloud"):
        with _env(**values):
            canva_token.push_secrets("my-project", run=run)
        writes = [(command, data) for command, data in commands if command[1] != "describe"]
        with _env(**_CLIENT):                                   # トークンが無ければ、何も登録しない
            assert "が設定されていません" in _fails(canva_token.push_secrets, "my-project", run)
    assert (["secrets", "versions", "add", "SHAPECHANGE_CANVA_CLIENT_ID", "--data-file=-", "--project=my-project"],
            b"client-id") in writes
    assert (["secrets", "create", "SHAPECHANGE_CANVA_REFRESH_TOKEN", "--replication-policy=automatic",
             "--data-file=-", "--project=my-project"], b"refresh") in writes
    assert len(writes) == 4 == len(commands) - 4
    assert not any(part in values.values() for command, _ in commands for part in command)     # 値は引数に出ない


def test_mcp_tool_reports_missing_settings_as_an_error():
    with _env():
        result = tools.refresh_canva_token()
    assert result["status"] == "error" and "SHAPECHANGE_CANVA_REFRESH_TOKEN" in result["message"]


def test_expired_access_token_points_to_the_refresh_tool():
    from src.canva import generate as canva

    def urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))

    with tempfile.TemporaryDirectory() as tmp, _env(SHAPECHANGE_CANVA_ACCESS_TOKEN="expired"), \
            patch.object(canva.urllib.request, "urlopen", urlopen):
        image = Path(tmp) / "final.png"
        image.write_bytes(b"image")
        message = _fails(canva.export_via_canva, image, Path(tmp) / "canva", "title", error=ProviderError)
    assert "shapechange_refresh_canva_token" in message and "--use-refresh-token" in message


def test_deploy_passes_the_canva_secrets_only_when_all_four_exist():
    spec = importlib.util.spec_from_file_location("deploy_cloud_run", _ROOT / "deploy" / "deploy_cloud_run.py")
    deploy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(deploy)
    assert sorted(deploy.CANVA_SECRETS) == sorted(canva_token.SECRET_NAMES)        # 2 か所の名前がずれない
    with contextlib.redirect_stdout(io.StringIO()):
        with patch.object(deploy, "gcloud", return_value="GEMINI_API_KEY\n" + "\n".join(deploy.CANVA_SECRETS)):
            assert deploy.canva_secrets() == deploy.CANVA_SECRETS
        with patch.object(deploy, "gcloud", return_value="GEMINI_API_KEY\nSHAPECHANGE_CANVA_ACCESS_TOKEN"):
            assert deploy.canva_secrets() == []


if __name__ == "__main__":
    from _runner import run_module_tests

    run_module_tests(globals())
