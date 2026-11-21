"""deploy/deploy_cloud_run.py — MCP サーバーを Cloud Run へ配置する（GeneratorsAI のコネクタと同じ設定）。

    python deploy/deploy_cloud_run.py --stage-only   # 送る内容を組み立てて確かめるだけ（GCP には触れない）
    python deploy/deploy_cloud_run.py                # 組み立て → ビルド → デプロイ

GitHub Actions（.github/workflows/deploy-mcp-server.yml）は、同じ組み立てと同じ設定を 2 段に分けて使う:

    python deploy/deploy_cloud_run.py --stage-dir DIR    # DIR に組み立てて残す（そこで docker build する）
    python deploy/deploy_cloud_run.py --image IMAGE      # ビルド済みのイメージを配置する

リポジトリのルートをそのままビルドに送らず、必要なものだけを一時フォルダーへ組み立てて送る:
コミット済みのコード（HEAD）、キャラクターデータ、``vm_bundle`` が書き出すシェーダーの 2 ファイル。
``VRCShader_ShapeChange`` のフォルダーや ``.env`` は入らない。手順と設定の説明は docs/deploy-cloud-run.md。
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.shape.vm_bundle import write_bundle  # noqa: E402

# プロジェクト ID とバケット名は、公開リポジトリに書かない。プロジェクトは環境変数 DEPLOY_GCP_PROJECT か
# gcloud の既定、バケットは環境変数 DEPLOY_GCS_BUCKET か配置済みのサービスの設定から読む（deploy() を参照）
PROJECT = os.getenv("DEPLOY_GCP_PROJECT", "").strip()
REGION = "asia-northeast1"
SERVICE = "shapechanger-ai"
TRACKED = ["Dockerfile", "requirements.txt", "requirements-mcp.txt", "src"]
DATA_DIRS = ["_creations-ai/ai-dataset", "_creations-ai/creations-db/data"]
DATA_TAR = "creations-data.tar"
# 秘密でない設定。モデルは VM の .env と同じ値にそろえる
ENV_VARS = {
    "MCP_TRANSPORT": "streamable-http",
    "OUTPUT_SINK": "drive",
    "GCS_PREFIX": "shapechange",             # 中間画像の置き場は GeneratorsAI と同じバケットで、接頭辞で分ける
    "GEMINI_REFERENCE_MODEL": "models/gemini-3.1-flash-image",
    "GPT_MODEL": "gpt-4o",
    "OPENAI_IMAGE_QUALITY": "standard",
}
# Secret Manager から渡す値。Canva は GeneratorsAI の連携を流用しないので入れない。
# MCP_AUTH_PASSPHRASE はコネクタの接続時に確認画面で入力する合言葉（GeneratorsAI と同じシークレット）
SECRETS = ["MCP_AUTH_PASSPHRASE", "GEMINI_API_KEY", "OPENAI_API_KEY", "DRIVE_FOLDER_ID", "DRIVE_CLIENT_ID", "DRIVE_CLIENT_SECRET",
           "DRIVE_REFRESH_TOKEN"]
# ShapeChangerAI 用の Canva 連携（任意）。4 件とも Secret Manager にあるときだけ渡す
# （登録は python -m src.tools.refresh_canva_token --push-secrets）
CANVA_SECRETS = ["SHAPECHANGE_CANVA_ACCESS_TOKEN", "SHAPECHANGE_CANVA_REFRESH_TOKEN",
                 "SHAPECHANGE_CANVA_CLIENT_ID", "SHAPECHANGE_CANVA_CLIENT_SECRET"]


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def stage(dest: Path) -> str:
    """``dest`` に、ビルドへ送るファイルだけを組み立て、配置するコミットを返す。"""
    commit = _git("rev-parse", "--short", "HEAD")
    print(f"配置するコミット: {commit}（{_git('rev-parse', '--abbrev-ref', 'HEAD')}）")
    if _git("status", "--porcelain", "--", *TRACKED):
        print("[WARN] 未コミットの変更があります。配置されるのはコミット済みの内容だけです。")
    archive = subprocess.run(["git", "-c", "core.autocrlf=false", "archive", "--format=tar", "HEAD", *TRACKED],
                             cwd=ROOT, check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest)
    # キャラクターデータは tar にまとめて送る。日本語のファイル名を含むため、Windows の gcloud にそのまま
    # 送らせると名前が UTF-8 でなくなり、Cloud Run がイメージを取り込めない（Container import failed）
    with tarfile.open(dest / DATA_TAR, "w", format=tarfile.PAX_FORMAT) as tar:
        for rel in DATA_DIRS:
            tar.add(ROOT / rel, arcname=rel)
        data_count = sum(member.isfile() for member in tar.getmembers())
    write_bundle(dest / "shape-data")

    files = [path for path in dest.rglob("*") if path.is_file()]
    shape = [path for path in files if path.is_relative_to(dest / "shape-data")]
    leaked = [path for path in files if "VRCShader_ShapeChange" in path.parts or path.name.startswith(".env")]
    if len(shape) != 2 or leaked:
        raise SystemExit(f"[ERROR] 送る内容が想定と違います（形状データ {len(shape)} 件、混入 {leaked[:3]}）。")
    size = sum(path.stat().st_size for path in files) / 1e6
    print(f"送る内容: {len(files)} ファイル / {size:.0f} MB（うち {DATA_TAR} にキャラクターデータ {data_count} 件、"
          f"形状データは {', '.join(sorted(p.name for p in shape))} の 2 件）")
    return commit


def gcloud(*args: str, capture: bool = False, quiet: bool = False) -> str:
    """gcloud を実行する。``quiet`` は出力を画面に出さない（GitHub Actions の公開ログに、gcloud が表示する
    サービスの URL を残さないため）。失敗したときだけ、URL を伏せたエラーの末尾を表示する。"""
    exe = shutil.which("gcloud")
    if not exe:
        raise SystemExit("[ERROR] gcloud が見つかりません。")
    command = [exe, *args, *([f"--project={PROJECT}"] if PROJECT else [])]
    if quiet:
        done = subprocess.run(command, text=True, capture_output=True)
        if done.returncode:
            tail = re.sub(r"https?://\S+", "<url>", done.stderr or "")[-2000:]
            raise SystemExit(f"[ERROR] gcloud {args[0]} {args[1]} が失敗しました:\n{tail}")
        return ""
    done = subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE if capture else None)
    return (done.stdout or "").strip()


def bucket() -> str:
    """中間画像を置くバケット。環境変数 DEPLOY_GCS_BUCKET が無ければ、配置済みのサービスの設定を引き継ぐ。"""
    name = os.getenv("DEPLOY_GCS_BUCKET", "").strip()
    if not name:
        try:
            service = json.loads(gcloud("run", "services", "describe", SERVICE, f"--region={REGION}",
                                        "--format=json", capture=True))
            containers = service["spec"]["template"]["spec"]["containers"]
            name = next((item.get("value", "") for item in containers[0].get("env", [])
                         if item.get("name") == "GCS_BUCKET"), "")
        except (subprocess.CalledProcessError, KeyError, IndexError, ValueError):
            name = ""
    if not name:
        raise SystemExit("[ERROR] バケット名が分かりません。環境変数 DEPLOY_GCS_BUCKET に指定してください。")
    return name


def canva_secrets() -> list[str]:
    """Canva の連携の 4 件が Secret Manager にそろっていれば、その名前を返す。欠けていれば渡さない
    （無いシークレットを指定すると、リビジョンを作れずデプロイが失敗する）。

    GitHub Actions のサービスアカウントはシークレットの一覧を読めない。一覧を読めないときは渡さず、
    環境変数 DEPLOY_CANVA_SECRETS=1 のときは確かめずに渡す（4 件を登録したあとに、ワークフローで指定する）。
    """
    if os.getenv("DEPLOY_CANVA_SECRETS", "").strip() == "1":
        print("Canva の連携（SHAPECHANGE_CANVA_*）を渡します（DEPLOY_CANVA_SECRETS=1）。")
        return CANVA_SECRETS
    try:
        listed = gcloud("secrets", "list", "--format=value(name.basename())", capture=True)
    except subprocess.CalledProcessError:
        print("Canva の連携は渡しません（Secret Manager の一覧を読めません）。Canva 仕上げは省かれます。")
        return []
    registered = set(listed.split())
    missing = [name for name in CANVA_SECRETS if name not in registered]
    if missing:
        print(f"Canva の連携は渡しません（Secret Manager に無い: {', '.join(missing)}）。Canva 仕上げは省かれます。")
        return []
    print("Canva の連携（SHAPECHANGE_CANVA_*）を渡します。")
    return CANVA_SECRETS


def issuer_url() -> str:
    """OAuth の issuer（サービスの URL）。環境変数 DEPLOY_ISSUER_URL が無ければ、プロジェクト番号から組み立てる。

    URL は「サービス名-プロジェクト番号.リージョン.run.app」で決まるので、デプロイ前に渡せる。GitHub Actions の
    サービスアカウントはプロジェクト番号を読めないので、リポジトリのシークレットから環境変数で受け取る。
    """
    url = os.getenv("DEPLOY_ISSUER_URL", "").strip().rstrip("/")
    if url:
        if not url.startswith("https://"):
            raise SystemExit("[ERROR] DEPLOY_ISSUER_URL は https:// で始まる URL を指定してください。")
        return url
    project = PROJECT or gcloud("config", "get-value", "project", capture=True)
    if not project:
        raise SystemExit("[ERROR] プロジェクトが分かりません。環境変数 DEPLOY_GCP_PROJECT か gcloud config で指定してください。")
    number = gcloud("projects", "describe", project, "--format=value(projectNumber)", capture=True)
    return f"https://{SERVICE}-{number}.{REGION}.run.app"


def deploy(commit: str, *, source: Path | None = None, image: str = "") -> str:
    """``source``（組み立てたフォルダー。Cloud Build がビルドする）か ``image``（ビルド済み）を配置する。"""
    url = issuer_url()
    env = ",".join(f"{key}={value}" for key, value in
                   {**ENV_VARS, "GCS_BUCKET": bucket(), "MCP_ISSUER_URL": url}.items())
    secrets = ",".join(f"{name}={name}:latest" for name in SECRETS + canva_secrets())
    target = f"--image={image}" if image else f"--source={source}"
    gcloud("run", "deploy", SERVICE, target, f"--region={REGION}", "--port=8080",
           "--allow-unauthenticated", "--min-instances=0", "--max-instances=1", "--memory=1Gi", "--timeout=3600",
           "--no-cpu-throttling", f"--set-env-vars={env}", f"--set-secrets={secrets}",
           f"--labels=commit={commit}", "--quiet",     # どのコミットが動いているかをサービスのラベルで追えるようにする
           quiet=bool(image))
    return url


USAGE = "使い方: python deploy/deploy_cloud_run.py [--stage-only | --stage-dir DIR | --image IMAGE]"


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["--stage-dir"] and len(args) == 2:
        dest = Path(args[1])
        if dest.exists() and any(dest.iterdir()):
            raise SystemExit(f"[ERROR] {dest} が空ではありません。空のフォルダーを指定してください。")
        dest.mkdir(parents=True, exist_ok=True)
        stage(dest)
        return 0
    if args[:1] == ["--image"] and len(args) == 2:
        deploy(_git("rev-parse", "--short", "HEAD"), image=args[1])
        print("配置しました。")
        return 0
    stage_only = args == ["--stage-only"]
    if args and not stage_only:
        print(USAGE, file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="shapechange-cloudrun-") as tmp:
        commit = stage(Path(tmp))
        if stage_only:
            return 0
        deploy(commit, source=Path(tmp))
    print("配置しました。コネクタの URL は、サービスの URL の末尾に /mcp を付けたものです"
          f"（gcloud run services describe {SERVICE} --region={REGION} --format=\"value(status.url)\"）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
