"""形状変化イラスト生成パイプラインの MCP サーバー (FastMCP)。

ツール:
- shapechange_generate_character     単体キャラクターの生成（5 段階、または quick）
- shapechange_generate_joint         複数キャラクターを 1 枚に（キャラクターごとに形状を指定）
- shapechange_generate_from_natural  自然文の依頼から生成
- shapechange_iterate                以前の実行を起点にした改稿（赤入れ画像、軽い加筆の quick）
- shapechange_create_upload_slot     入力画像（加筆の起点・赤入れ・構図参考・参考画像）の受け取り口
- shapechange_job_status             ジョブの進捗と完成画像のリンク
- shapechange_answer_reference_warning  Stage 2 の観察失敗に対する続行／中止の回答
- shapechange_list_runs / shapechange_get_run_logs  実行の一覧と記録
- shapechange_list_gcs_logs / shapechange_get_gcs_image  GCS に残っている画像の一覧と取得
- shapechange_refresh_canva_token    Canva のアクセストークンの更新
- shapechange_list_modes / shapechange_list_characters  指定できる形状とキャラクター

生成は数分かかるため、生成系のツールはジョブを登録してすぐ job_id を返す。生成系のツールに
``dry_run`` を付けると、ジョブを登録せず、プロンプトと役割の付いた画像の一覧をその場で返す。

起動:
    python -m src.mcp_server                                    # ローカル (stdio)
    MCP_TRANSPORT=streamable-http PORT=8001 python -m src.mcp_server   # リモート
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager, redirect_stdout
from typing import Any, Literal, Optional

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from ..pipeline.shape_pipeline import _load_env
from . import output_sink, tools
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
ImageRole = Literal["base", "markup", "composition", "reference"]
Provenance = Literal["own_generated", "own_work", "own_photo"]
_DRY_RUN = "true にすると生成せず、プロンプトと役割の付いた画像の一覧を返す（画像の生成は呼ばない）。生成の前に必ず確かめる"
_WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True}
_READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InputImage(_Base):
    object_key: str = Field(..., min_length=1, max_length=1024,
                            description="shapechange_create_upload_slot で上げた画像、または shapechange_list_gcs_logs の object_key")
    role: ImageRole = Field(..., description="base: 加筆の起点（1 枚）/ markup: base に赤で指示を描き込んだ画像（1 枚。base か起点の実行が必要）"
                                             "/ composition: 構図参考（1 枚。ポーズ・配置・画角だけを借りる。色は自動で抜く）"
                                             "/ reference: 生成画像と関係のない参考（3 枚まで。note が必須）")
    note: str = Field("", max_length=200, description="何の参考にするか。role が reference のときは必須")
    provenance: Provenance = Field(..., description="画像の出所。own_generated: このパイプラインの生成結果 / own_work: 利用者自身の作品"
                                                    " / own_photo: 利用者自身が撮った写真。利用者に確かめた値を入れる（推測しない）。"
                                                    "第三者の作品は渡せない")


def _images_field() -> Any:
    return Field(default_factory=list, max_length=6,
                 description="入力画像。キャラクターの外見は公式画像と設定資料が優先される")


class _Common(_Base):
    images: list[InputImage] = _images_field()
    dry_run: bool = Field(False, description=_DRY_RUN)
    scene: str = Field("", max_length=600, description="場面・背景の指定")
    style: str = Field("", max_length=300, description="画風の指定（既定は公式画像に合わせる）")
    extra: str = Field("", max_length=600, description="追加の作画指定")
    count: int = Field(1, ge=1, le=4, description="完成画像の枚数")
    rough_count: Optional[int] = Field(None, ge=1, le=5, description="ラフの枚数（既定は単体 3、合同は 1 体あたり 2）")
    skip_canva: bool = Field(False, description="Canva 仕上げを省く")
    provider: Provider = "gemini"
    work: Optional[str] = Field(None, description="作品キー（例: NumberTales, SinisterChangingGirls）")


class GenerateCharacterInput(_Common):
    badge: Optional[str] = Field(None, description="バッジ（例: 57, 2B, EZ1, ENE）。作品コード付き（NTS-57, SAR-EZ1）なら work は不要。"
                                                   "番号と作品内の識別子（2-alt, Mouse）も使える")
    name: Optional[str] = Field(None, description="名前の一部（badge の代わりに指定）")
    mode: int = Field(..., ge=0, le=15, description="形状番号（shapechange_list_modes で一覧）")
    form: Optional[Literal["humanoid", "corefolder"]] = Field(None, description="変形前の形態")
    state: str = Field("", max_length=200, description="変形前の姿の状態。shapechange_list_characters の states にある名前"
                                                       "（afterChanged, beasted など）か、体型と着用物だけを変える自由な文。省くと既定の状態")
    costume: str = Field("", max_length=200, description="変形前の姿の衣装。shapechange_list_characters の costumes にある名前"
                                                         "（miko など）か、衣装を変える自由な文。省くと通常衣装")
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
    state: str = Field("", max_length=200, description="このキャラクターの変形前の状態（単体生成の state と同じ）")


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
    dry_run: bool = Field(False, description="true にすると画像を生成せず、依頼文の読み取り結果とプロンプトを返す"
                                             "（依頼文の読み取りには OpenAI の API を使う）")


class IterateInput(_Base):
    run_id: str = Field(..., description="起点にする実行（shapechange_list_runs の run_id）")
    revisions: list[str] = Field(default_factory=list, description="前回から変える点。赤入れ画像（role: markup）だけで指示する場合は省ける")
    images: list[InputImage] = _images_field()
    quick: bool = Field(False, description="true にすると、起点の画像の編集 1 回だけで仕上げる（観察・検査・Canva を省き、完成画像は 1 枚）")
    dry_run: bool = Field(False, description=_DRY_RUN)
    count: Optional[int] = Field(None, ge=1, le=4)
    skip_canva: bool = False


class UploadSlotInput(_Base):
    content_type: Literal["image/png", "image/jpeg", "image/webp"] = Field(..., description="上げる画像の形式")


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


class ListGcsLogsInput(_Base):
    limit: int = Field(50, ge=1, le=2000, description="返す最大件数（既定 50）")
    since_days: int = Field(49, ge=1, le=3650, description="この日数以内に作られた画像を対象にする（既定 49 日）")


class GcsImageInput(_Base):
    object_key: str = Field(..., min_length=1, max_length=1024,
                            description="shapechange_list_gcs_logs の object_key（ファイル名だけでもよい）")


class ListCharactersInput(_Base):
    work: Optional[str] = None


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


async def _generate(kind: str, argv: list[str], dry_run: bool) -> str:
    """``dry_run`` なら計画だけを返し、そうでなければジョブを登録する。"""
    if dry_run:
        return _json(await asyncio.to_thread(tools.dry_run, argv))
    return _json(tools.submit(kind, argv))


@mcp.tool(name="shapechange_generate_character", annotations={"title": "形状変化イラストを生成（単体）", **_WRITE})
async def shapechange_generate_character(params: GenerateCharacterInput) -> str:
    """キャラクター 1 体が指定の形状へ変化した姿を生成する。課金を伴う。ジョブを登録して job_id を返す。
    先に dry_run: true で内容を確かめ、利用者の承認を得てから呼ぶ。images で入力画像を渡せる。"""
    data = params.model_dump()
    dry_run = data.pop("dry_run")
    return await _generate("generate_character", tools.build_argv(data), dry_run)


@mcp.tool(name="shapechange_generate_joint", annotations={"title": "形状変化イラストを生成（合同）", **_WRITE})
async def shapechange_generate_joint(params: GenerateJointInput) -> str:
    """2〜4 体を 1 枚に描く。キャラクターごとに別の形状を指定できる。課金を伴う。
    images の入力画像は、全員を描く清書でだけ使う（赤入れが反映されたかの検査は行わない）。"""
    data = params.model_dump()
    dry_run = data.pop("dry_run")
    return await _generate("generate_joint", tools.build_argv(data), dry_run)


@mcp.tool(name="shapechange_generate_from_natural", annotations={"title": "自然文から形状変化イラストを生成", **_WRITE})
async def shapechange_generate_from_natural(params: GenerateFromNaturalInput) -> str:
    """自然文の依頼からキャラクターと形状を読み取って生成する。複数に読める形状語は代表の形状に決め、
    読み取り結果は job_status の result.interpretation に入る。課金を伴う。入力画像は渡せない。"""
    data = params.model_dump()
    dry_run = data.pop("dry_run")
    return await _generate("generate_from_natural", ["--natural", data.pop("request")] + tools.build_argv(data), dry_run)


@mcp.tool(name="shapechange_iterate", annotations={"title": "以前の生成結果を改稿", **_WRITE})
async def shapechange_iterate(params: IterateInput) -> str:
    """以前の実行と同じ指定で、その完成画像を起点に Stage 3〜5 をやり直す。課金を伴う。
    変える点は revisions の文か、起点の画像に赤で描き込んだ画像（images の role: markup）で伝える。両方あれば両方使う。
    quick: true は起点の画像の編集 1 回だけ。サーバーが止まったあとの実行も、GCS に残っていれば起点にできる。"""
    if not params.revisions and not any(image.role == "markup" for image in params.images):
        return _json({"status": "error", "message": "revisions（変える点）か、赤入れ画像（images の role: markup）のどちらかを指定してください。"})
    overrides = {"images": [image.model_dump() for image in params.images], "quick": params.quick,
                 "count": params.count or (1 if params.quick else None), "skip_canva": params.skip_canva}
    try:
        argv = await asyncio.to_thread(tools.iterate_argv, params.run_id, params.revisions, overrides)
    except ValueError as err:
        return _json({"status": "error", "message": str(err)})
    return await _generate("iterate", argv, params.dry_run)


@mcp.tool(name="shapechange_create_upload_slot", annotations={"title": "入力画像の受け取り口を作る", **_WRITE})
async def shapechange_create_upload_slot(params: UploadSlotInput) -> str:
    """入力画像 1 枚ぶんの署名付き PUT URL（15 分有効）と object_key を返す。1 枚 10MB まで、PNG・JPEG・WebP。
    返ってきた upload_command のとおりに画像を上げ、object_key を生成ツールの images に渡す。
    上げてよいのは、このパイプラインの生成結果と利用者自身の作品だけ。出所は 1 枚ずつ利用者に確かめる。"""
    return _json(await asyncio.to_thread(tools.create_upload_slot, params.content_type))


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


@mcp.tool(name="shapechange_list_gcs_logs", annotations={"title": "GCS に残っている画像の一覧", **_READ})
async def shapechange_list_gcs_logs(params: ListGcsLogsInput) -> str:
    """GCS に残っている中間画像を、新しい順に署名付き URL と一緒に返す。バケットを直接見るので、
    サーバーが再起動してジョブや実行の記録が消えたあとも使える。"""
    return _json(await asyncio.to_thread(output_sink.list_gcs_logs, params.limit, params.since_days))


@mcp.tool(name="shapechange_get_gcs_image", annotations={"title": "GCS の画像を取得", **_READ})
async def shapechange_get_gcs_image(params: GcsImageInput) -> str:
    """GCS の画像 1 件を base64 で返す。署名付き URL を開けない環境で使う。"""
    result = await asyncio.to_thread(output_sink.fetch_gcs_image_b64, params.object_key)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(name="shapechange_refresh_canva_token", annotations={"title": "Canva のアクセストークンを更新", **_WRITE})
async def shapechange_refresh_canva_token() -> str:
    """ShapeChangerAI 用の Canva 連携のアクセストークンを、リフレッシュトークンで更新する。Canva 仕上げが
    401 で失敗したときに呼ぶ。結果の saved_to が空なら、新しいトークンはサーバーが止まると失われる。"""
    return _json(await asyncio.to_thread(tools.refresh_canva_token))


@mcp.tool(name="shapechange_list_modes", annotations={"title": "形状の一覧", **_READ})
async def shapechange_list_modes() -> str:
    """指定できる 16 形状と寸法（変形前の身長を 1 とした比）を返す。"""
    return _json(tools.list_modes())


@mcp.tool(name="shapechange_list_characters", annotations={"title": "キャラクターの一覧", **_READ})
async def shapechange_list_characters(params: ListCharactersInput) -> str:
    """AI 利用が許可されたキャラクターを返す。名前で選べる状態（states）と衣装（costumes）があれば添える。"""
    return _json(tools.list_characters(params.work))


def main() -> None:
    raw = (os.getenv("MCP_TRANSPORT") or "stdio").strip().lower()
    transport = "streamable-http" if raw in ("http", "streamable", "streamable-http") else "sse" if raw == "sse" else "stdio"
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
