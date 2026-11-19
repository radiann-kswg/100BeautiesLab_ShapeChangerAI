#!/usr/bin/env bash
# setup_vm.sh — GCE VM（Debian 13）に 100BeautiesLab_ShapeChangerAI の SSH/CLI 実行基盤を作る。
# VM 上で `bash setup_vm.sh` として実行する（sudo できる自分のユーザー。配置先の所有者もこのユーザー）。
# 何度実行しても壊れない（clone 済みなら取得し直し、venv 済みなら再利用、.env は毎回作り直す）。
# 手順の全体は docs/deploy-vm.md。
#
# - コードは公開リポジトリ（100BeautiesLab_ShapeChangerAI-Public）から HTTPS で取得する。鍵は要らない。
# - サブモジュールは _creations-ai（キャラクターデータ）と VRCShader_ShapeChange（公開用のシェーダー
#   パッケージ）を取得する。どちらも公開リポジトリで、モデルや検証用のデータは入っていない。
# - .env の秘密の値は Secret Manager から読む（VM のサービスアカウントで取得。PC を経由しない）。
set -euo pipefail

APP_DIR=/opt/shapechanger
BRANCH="${SHAPECHANGE_BRANCH:-main}"
REPO=https://github.com/radiann-kswg/100BeautiesLab_ShapeChangerAI-Public.git
# Secret Manager から読む値。Canva は GeneratorsAI の連携を流用しないので入れない
SECRETS="GEMINI_API_KEY OPENAI_API_KEY DRIVE_FOLDER_ID DRIVE_CLIENT_ID DRIVE_CLIENT_SECRET DRIVE_REFRESH_TOKEN"
# 作り直しても既存の .env から引き継ぐ行（秘密でない設定）
KEEP='GEMINI_IMAGE_MODEL|GEMINI_REFERENCE_MODEL|GEMINI_IMAGE_SLEEP|OPENAI_IMAGE_MODEL|OPENAI_IMAGE_QUALITY|GPT_MODEL|GCS_BUCKET|GCS_PREFIX'

die() { echo "[ERROR] $*" >&2; exit 1; }

echo "[1/5] apt: git / python3-venv"
sudo apt-get update -qq
sudo apt-get install -y -qq git python3 python3-venv python3-pip >/dev/null

echo "[2/5] clone or fetch -> $APP_DIR（ブランチ: $BRANCH、所有者: $USER）"
sudo mkdir -p "$APP_DIR"
sudo chown "$USER:$USER" "$APP_DIR"
if [ ! -d "$APP_DIR/.git" ]; then
  git clone -q --branch "$BRANCH" "$REPO" "$APP_DIR"
else
  # 非公開リポジトリをデプロイキーで clone していた頃の VM も、ここで公開側へ切り替わる。
  # 公開側は履歴が別系統なので、pull ではなく取得した内容へ合わせる（追跡ファイルの手元の変更は捨てる）
  git -C "$APP_DIR" config --unset core.sshCommand || true
  git -C "$APP_DIR" remote set-url origin "$REPO"
  git -C "$APP_DIR" fetch -q --prune origin
  git -C "$APP_DIR" checkout -q -f -B "$BRANCH" "origin/$BRANCH"
fi
git -C "$APP_DIR" submodule sync -q --recursive
git -C "$APP_DIR" submodule update -q --init --depth 1 _creations-ai VRCShader_ShapeChange
git -C "$APP_DIR/_creations-ai" submodule update -q --init --depth 1 --recursive
echo "  $(git -C "$APP_DIR" log -1 --format='%h %s')"

echo "[3/5] venv + requirements-mcp.txt"
[ -x "$APP_DIR/.venv/bin/python" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements-mcp.txt"

echo "[4/5] .env を Secret Manager から作り直す"
kept=$(grep -E "^($KEEP)=" "$APP_DIR/.env" 2>/dev/null || true)
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
for name in $SECRETS; do
  value=$(gcloud secrets versions access latest --secret="$name" 2>/dev/null) \
    || die "Secret Manager から $name を読めません（VM のサービスアカウントの権限とスコープを確認）。"
  printf '%s=%s\n' "$name" "$value" >> "$tmp"
done
printf '%s\n' "OUTPUT_SINK=drive" "MCP_TRANSPORT=stdio" >> "$tmp"
[ -z "$kept" ] || printf '%s\n' "$kept" >> "$tmp"
install -m 600 "$tmp" "$APP_DIR/.env"
echo "  .env（600）。キー名のみ表示: $(grep -o '^[A-Z_]*=' "$APP_DIR/.env" | tr -d '=' | tr '\n' ' ')"

echo "[5/5] 自己チェック（生成 API は呼ばない）"
cd "$APP_DIR"
.venv/bin/python -c "import google.genai, openai, mcp, googleapiclient, dotenv, PIL; print('  imports OK')"
.venv/bin/python -m src.pipeline.shape_pipeline --list-modes >/dev/null
echo "  --list-modes OK（シェーダーのサブモジュールから寸法を読めた）"
# tests/ は開発用の作業フォルダーが前提なので、VM では dry-run で確かめる
.venv/bin/python -m src.pipeline.shape_pipeline --badge 85 --mode 0 --dry-run >/dev/null
echo "  --dry-run OK（キャラクターデータと参照画像）"
[ ! -d /opt/shapechanger-shape ] || echo "  /opt/shapechanger-shape は以前の配置の残りで、もう使いません（消して構いません）。"
df -h / | tail -1 | awk '{print "  disk used " $3 " / " $2 " (" $5 ")"}'
echo "done."
