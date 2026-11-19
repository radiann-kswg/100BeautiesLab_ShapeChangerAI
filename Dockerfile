# 形状変化イラスト生成の MCP サーバー（Cloud Run 用）。
# リポジトリのルートからはビルドしない。deploy/deploy_cloud_run.py が組み立てたフォルダー
# （コード・キャラクターデータ・シェーダーの 2 ファイルだけ）をビルドの入力にする。
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    HOST=0.0.0.0 \
    MCP_TRANSPORT=streamable-http \
    OUTPUT_SINK=drive \
    SHAPECHANGE_REPO_PATH=/app/shape-data

WORKDIR /app

COPY requirements.txt requirements-mcp.txt ./
RUN pip install -r requirements-mcp.txt

COPY src/ ./src/
# キャラクターデータ（_creations-ai/ 以下）。ADD が tar を展開する
ADD creations-data.tar ./
COPY shape-data/ ./shape-data/

EXPOSE 8080

CMD ["python", "-m", "src.mcp_server"]
