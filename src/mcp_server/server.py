"""形状変化イラスト生成パイプラインの MCP サーバー (FastMCP)。

ツール:
- shapechange_generate_character     単体キャラクターの生成（5 段階、または quick）
- shapechange_generate_joint         複数キャラクターを 1 枚に（キャラクターごとに形状を指定）
- shapechange_generate_from_natural  自然文の依頼から生成
- shapechange_iterate                以前の実行を起点にした改稿
- shapechange_job_status             ジョブの進捗と完成画像のリンク
- shapechange_answer_reference_warning  Stage 2 の観察失敗に対する続行／中止の回答
- shapechange_list_runs / shapechange_get_run_logs  実行の一覧と記録
- shapechange_list_modes / shapechange_list_characters  指定できる形状とキャラクター

生成は数分かかるため、生成系のツールはジョブを登録してすぐ job_id を返す。

起動:
    python -m src.mcp_server                                    # ローカル (stdio)
    MCP_TRANSPORT=streamable-http PORT=8001 python -m src.mcp_server   # リモート
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import asynccontextmanager, redirect_stdout
from typing import Any, Literal, Optional

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from ..pipeline.shape_pipeline import _load_env
from . import tools
from .auth import SimpleOAuthProvider
from .jobs import MANAGER

_load_env()

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "").strip() or "8001")
# HTTP で公開する場合の URL（Cloud Run ではサービスの URL）。設定すると OAuth が有効になる。
# SimpleOAuthProvider は認可を自動承認する個人用の実装で、利用者の本人確認はしない。
ISSUER_URL = os.getenv("MCP_ISSUER_URL", "").rstrip("/")


@asynccontextmanager
async def _lifespan(server):
    # パイプラインの print が stdio の通信に混ざらないよう、標準出力を標準エラーへ向ける
    with redirect_stdout(sys.stderr):
        yield {}


mcp = FastMCP(
    "shapechange_mcp",
    host=HOST,
    port=PORT,
    auth_server_provider=SimpleOAuthProvider() if ISSUER_URL else None,
    auth=AuthSettings(issuer_url=ISSUER_URL, resource_server_url=None,
                      client_registration_options=ClientRegistrationOptions(enabled=True)) if ISSUER_URL else None,
    lifespan=_lifespan,
)

Limb = Literal["keep", "fold", "hide"]
Appendages = Literal["keep", "integrate"]
Provider = Literal["gemini", "openai"]
_WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True}
_READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Common(_Base):
    scene: str = Field("", max_length=600, description="場面・背景の指定")
    style: str = Field("", max_length=300, description="画風の指定（既定は公式画像に合わせる）")
    extra: str = Field("", max_length=600, description="追加の作画指定")
    count: int = Field(1, ge=1, le=4, description="完成画像の枚数")
    rough_count: Optional[int] = Field(None, ge=1, le=5, description="ラフの枚数（既定は単体 3、合同は 1 体あたり 2）")
    skip_canva: bool = Field(False, description="Canva 仕上げを省く")
    provider: Provider = "gemini"
    work: Optional[str] = Field(None, description="作品キー（例: NumberTales, SinisterChangingGirls）")


class GenerateCharacterInput(_Common):
    badge: Optional[str] = Field(None, description="番号・バッジ・作品内の識別子（例: 57, 2B, ENE）")
    name: Optional[str] = Field(None, description="名前の一部（badge の代わりに指定）")
    mode: int = Field(..., ge=0, le=15, description="形状番号（shapechange_list_modes で一覧）")
    form: Optional[Literal["humanoid", "corefolder"]] = Field(None, description="変形前の形態")
    layout: Literal["single", "sequence", "five-view"] = "single"
    amount: float = Field(1.0, gt=0, le=1, description="変形量。1 未満は途中の姿（layout=single のみ）")
    size: float = Field(1.0, ge=0.2, le=2.0, description="形状全体の大きさ")
    arms: Optional[Limb] = None
    legs: Optional[Limb] = None
    appendages: Optional[Appendages] = None
    quick: bool = Field(False, description="検査なしで 1 回だけ生成する")


class JointSubject(_Base):
    id: str = Field(..., description="番号・バッジ、または名前の一部")
    mode: int = Field(..., ge=0, le=15)


class GenerateJointInput(_Common):
    subjects: list[JointSubject] = Field(..., min_length=2, max_length=4)
    arms: Optional[Limb] = None
    legs: Optional[Limb] = None
    appendages: Optional[Appendages] = None


class GenerateFromNaturalInput(_Base):
    request: str = Field(..., min_length=2, max_length=600, description="例: 85(ハッコ)が箱化している様子")
    work: Optional[str] = None
    provider: Provider = "gemini"
    skip_canva: bool = False


class IterateInput(_Base):
    run_id: str = Field(..., description="起点にする実行（shapechange_list_runs の run_id）")
    revisions: list[str] = Field(..., min_length=1, description="前回から変える点")
    count: Optional[int] = Field(None, ge=1, le=4)
    skip_canva: bool = False


class JobInput(_Base):
    job_id: str


class ReferenceDecisionInput(_Base):
    job_id: str
    request_id: str = Field(..., description="job_status の confirmation.request_id")
    proceed: bool = Field(..., description="true: 画像観察なしで続行 / false: 中止")


class ListRunsInput(_Base):
    limit: int = Field(20, ge=1, le=100)


class RunInput(_Base):
    run_id: str


class ListCharactersInput(_Base):
    work: Optional[str] = None


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


@mcp.tool(name="shapechange_generate_character", annotations={"title": "形状変化イラストを生成（単体）", **_WRITE})
async def shapechange_generate_character(params: GenerateCharacterInput) -> str:
    """キャラクター 1 体が指定の形状へ変化した姿を生成する。課金を伴う。ジョブを登録して job_id を返す。"""
    return _json(tools.submit("generate_character", tools.build_argv(params.model_dump())))


@mcp.tool(name="shapechange_generate_joint", annotations={"title": "形状変化イラストを生成（合同）", **_WRITE})
async def shapechange_generate_joint(params: GenerateJointInput) -> str:
    """2〜4 体を 1 枚に描く。キャラクターごとに別の形状を指定できる。課金を伴う。"""
    return _json(tools.submit("generate_joint", tools.build_argv(params.model_dump())))


@mcp.tool(name="shapechange_generate_from_natural", annotations={"title": "自然文から形状変化イラストを生成", **_WRITE})
async def shapechange_generate_from_natural(params: GenerateFromNaturalInput) -> str:
    """自然文の依頼からキャラクターと形状を読み取って生成する。複数に読める形状語は代表の形状に決め、
    読み取り結果は job_status の result.interpretation に入る。課金を伴う。"""
    data = params.model_dump()
    return _json(tools.submit("generate_from_natural", ["--natural", data.pop("request")] + tools.build_argv(data)))


@mcp.tool(name="shapechange_iterate", annotations={"title": "以前の生成結果を改稿", **_WRITE})
async def shapechange_iterate(params: IterateInput) -> str:
    """以前の実行と同じ指定で、その完成画像を起点に Stage 3〜5 をやり直す。課金を伴う。"""
    try:
        argv = tools.iterate_argv(params.run_id, params.revisions,
                                  {"count": params.count, "skip_canva": params.skip_canva})
    except ValueError as err:
        return _json({"status": "error", "message": str(err)})
    return _json(tools.submit("iterate", argv))


@mcp.tool(name="shapechange_job_status", annotations={"title": "ジョブの進捗", **_READ})
async def shapechange_job_status(params: JobInput) -> str:
    """ジョブの状態を返す。status が awaiting_confirmation のときは confirmation の理由と質問を
    利用者に示し、回答を得てから shapechange_answer_reference_warning を呼ぶ（回答を推測しない）。"""
    return _json(tools.job_status(params.job_id))


@mcp.tool(name="shapechange_answer_reference_warning",
          annotations={"title": "画像観察の失敗への回答", **_WRITE, "openWorldHint": False})
async def shapechange_answer_reference_warning(params: ReferenceDecisionInput) -> str:
    """Stage 2 の画像観察が失敗したジョブに、利用者の続行／中止の回答を伝える。無回答は 30 分で中止になる。"""
    try:
        MANAGER.answer_reference(params.job_id, params.request_id, params.proceed)
    except ValueError as err:
        return _json({"status": "error", "message": str(err)})
    return _json({"status": "ok", "proceed": params.proceed})


@mcp.tool(name="shapechange_list_runs", annotations={"title": "実行の一覧", **_READ})
async def shapechange_list_runs(params: ListRunsInput) -> str:
    """これまでの実行を新しい順に返す。"""
    return _json(tools.list_runs(params.limit))


@mcp.tool(name="shapechange_get_run_logs", annotations={"title": "実行の記録", **_READ})
async def shapechange_get_run_logs(params: RunInput) -> str:
    """実行 1 件のプロンプト・条件・観察・検査ログ・中間画像を返す。"""
    return _json(tools.run_logs(params.run_id))


@mcp.tool(name="shapechange_list_modes", annotations={"title": "形状の一覧", **_READ})
async def shapechange_list_modes() -> str:
    """指定できる 16 形状と寸法（変形前の身長を 1 とした比）を返す。"""
    return _json(tools.list_modes())


@mcp.tool(name="shapechange_list_characters", annotations={"title": "キャラクターの一覧", **_READ})
async def shapechange_list_characters(params: ListCharactersInput) -> str:
    """AI 利用が許可されたキャラクターを返す。"""
    return _json(tools.list_characters(params.work))


def main() -> None:
    raw = (os.getenv("MCP_TRANSPORT") or "stdio").strip().lower()
    transport = "streamable-http" if raw in ("http", "streamable", "streamable-http") else "sse" if raw == "sse" else "stdio"
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
