# src — 形状変化イラスト生成パイプライン

創作キャラクターのデータと形状変化シェーダーの寸法からプロンプトを組み立て、ラフの生成・検査・修正・清書までを行う。作業ルールは [AGENTS.md](../AGENTS.md) が正本。合意した要件と保留事項は [docs/requirements-5stage-connector.md](../docs/requirements-5stage-connector.md)。

## 入力

| 入力 | 読む場所 | 使う内容 |
| --- | --- | --- |
| キャラクター | `_creations-ai/ai-dataset/manifest.jsonl` | 名前・身長・外見の特徴・尾の構造・配色・AI 利用可否・公式画像のパス |
| 参照画像 | `_creations-ai/creations-db/data/` | 対象キャラクター本人の公式イラスト・設定画・尾の部位図・デザイン表（最大 4 枚） |
| 形状の寸法 | `VRCShader_ShapeChange/`（公開用のシェーダーパッケージ）の `ShapeChange.hlsl` と `ShapeChangeProperties.lilblock` | 16 形状の完成寸法（変形前の身長を 1 とした比）、厚み、顔の比率、四肢と付属物の既定 |
| 形状の記述 | [`shape/mode_descriptions.json`](shape/mode_descriptions.json) | 輪郭・顔の位置・面と衣装の割り当て・途中の姿（2D 作画向けの言葉） |

寸法はこのリポジトリへ写さず、実行のたびにシェーダーから読む。シェーダーの記述が変わって読めなくなった場合は、古い値で続けずエラーで止まる。読むのはこの 2 ファイルだけ。

外見の要約は、CreationsDB の構造再同期で更新される `ai_hints.forms.<形態>.ai_tags` を優先する。タグがない場合だけ `silhouette_notes.body_description`・`attached_items` と `outfit_features` を使う。手書きの旧説明を新しいタグへ混ぜない。`negative_visuals` は `negative_keywords` とともに生成時に避ける要素へ渡すが、正のタグと同じ項目（前後の空白・大小文字は無視）は除外する。`AppearanceDetail.Laterality` の左右はキャラクター本人の身体が基準で、配色の `Note_EN` / `Note_JP`（色を使う部位や条件の注記）もプロンプト・観察・検査に残す。

共通・形態別の AIHints から要約へ取り込むリストでは、`TODO:` で始まる未記入項目（先頭の空白・大小文字は無視）を除外する。原典や manifest の内容は変更しない。

`AppearanceDetail` の属性は、辞書コード（`vdict_EarShapeType` など）を末尾の語で、個体差の補足（`about_EN` / `about_JP`）を括弧で、本数（`value_Num`）を `count: 1` の形で要約へ入れる。項目の `Note_EN` / `Note_JP`（通常制服などの適用範囲）も残す。`TailsUnit` を持たない作品では、尾の項目に書かれた本数を Stage 4 の本数の照合に使う。

`Formation` が `humanoid` / `corefolder` 以外の項目は、作品内の状態（豹変系女子の `beforeChange` / `afterChanged`）として扱う。`beforeChange` は通常の項目として描く。それ以外の状態の項目は「別状態だけの特徴。今回の絵には描かない」と付けて要約に残す。公式設定画には両方の状態が描かれているため、項目を捨てると観察と検査が通常の姿の特徴と取り違える。状態を選んで描くオプションはない。

`AppearanceDetail` の `Costume`（衣装）が `usual` 以外の項目は、manifest を読む時点で落とす。AIHints に写された同じ項目の文面も落とす。描くのは通常衣装だけで、別衣装（NumberTales 35 の `miko`、60 の `otaku` など）の特徴を混ぜない。`Costume` を持たない項目は、どの衣装でも描く特徴として残す。衣装を選んで描くオプションはない。

権利上 AI に渡さない画像（`_AI_EXCLUDED_IMAGE_STEMS`）は、同じ時点でレコードの画像一覧から外す。いまの対象は、NumberTales 61 のアイドル衣装（衣装デザインが第三者によるもの）を描いた 3 枚。上流で衣装・画像の単位の指定ができるようになるまでの、このリポジトリ側の除外。

作品メタの共通色（獣爾騎兵の制服共通色 `$EnumDef_CommonColor`）は manifest に無く、どのキャラクターが使うかの記録も無いため、配色へ足さない。別作品のレコードからの外見の継承（豹変系女子の N が NumberTales の 0 を参照する設定）も manifest に展開されないため、要約には入らない。

## 5 段階

| Stage | すること | 使うサービス |
| --- | --- | --- |
| 1 | キャラクターと形状の仕様からプロンプトを組む（同じ入力なら同じプロンプト） | なし |
| 2 | 公式設定画を観察して、見えている特徴を言葉にする。失敗時は続行／中止を確認し、無回答は中止 | OpenAI（`GPT_MODEL`） |
| 3 | ラフを生成する（単体 3 枚、合同は 1 体あたり 2 枚） | Gemini / OpenAI の画像モデル |
| 4 | ラフを検査する。形状と違反の指摘が 3 件以上なら作り直し、それ未満はラフを起点に修正する | OpenAI（`GPT_MODEL`）+ 画像モデル |
| 5 | 検査を通ったラフから完成画像を清書し、Canva の連携があれば仕上げて書き出す | 画像モデル + Canva |

Stage 4 では、尾の本数と体数を「設定を知らせない別の呼び出し」で数えさせ、設定との照合をコードで行う。数え方には 1〜2 本の誤差がある。検査に失敗したラフは未検査と記録して通す。

## 使い方

```sh
pip install -r requirements.txt

# 形状の一覧と寸法 / AI 利用が許可されたキャラクターの一覧
python -m src.pipeline.shape_pipeline --list-modes
python -m src.pipeline.shape_pipeline --list-characters --work NumberTales

# プロンプトと参照画像の確認（生成しない・課金なし）
python -m src.pipeline.shape_pipeline --badge 57 --mode 4 --dry-run

# 5 段階で生成 / 検査なしで 1 回だけ生成
python -m src.pipeline.shape_pipeline --badge 57 --mode 4
python -m src.pipeline.shape_pipeline --badge 57 --mode 4 --quick

# 複数キャラクターを 1 枚に（キャラクターごとに形状を指定）
python -m src.pipeline.shape_pipeline --joint 85:0,73:3 --scene "公園の芝生の上"

# 自然文の依頼（読み取ったキャラクターと形状を表示してから進む）
python -m src.pipeline.shape_pipeline --natural "85(ハッコ)が箱化している様子" --dry-run

# 改稿（以前の完成画像を起点に、変える点を指定してやり直す）
python -m src.pipeline.shape_pipeline --badge 57 --mode 4 \
    --iterate-from 20261118_130500_gemini_mode04_NumberTales-57 --revisions "尾を 7 本そろえる"
```

生成は課金を伴う。先に `--dry-run` でプロンプトと参照画像を確認する。5 段階は 1 回の実行で、画像生成が最大 7 回ほど（ラフ 3、修正最大 3、完成 1）と Vision の呼び出しが 7 回ほどかかる。

PowerShell で `python` が Microsoft Store のエイリアスに当たって起動しない場合は、`python` を `py` に読み替える（例: `py -m src.pipeline.shape_pipeline --list-modes`）。

### 主なオプション

| オプション | 内容 |
| --- | --- |
| `--badge` / `--name` / `--work` | キャラクターの指定。`--badge` は CreationsDB のバッジ（`57`, `2B`, `EZ1`, `ENE`）。作品コード付き（`NTS-57`, `SAR-EZ1`）なら `--work` は要らない。番号と作品内の識別子（`2-alt`, `Mouse`）も使える。`--name` は名前の一部。バッジは `--list-characters` の 2 列目に出る |
| `--joint` | `ID:形状番号` を 2〜4 件（例 `85:0,73:3` / `SAR-EZ1:0,SAR-EZ4:3`）。ID はバッジ・番号・名前の一部 |
| `--natural` | 自然文の依頼。複数に読める形状語は代表の形状に決め打ちする（表は `pipeline/natural_parser.py`） |
| `--form` | 変形前の形態（`humanoid` / `corefolder`。形態を持つ作品のみ） |
| `--mode` | 形状番号 0〜15 |
| `--layout` | `single`（1 体）/ `sequence`（変形前→途中→完了）/ `five-view`（五方向図） |
| `--amount` | 変形量。1 未満で途中の姿（`single` のみ） |
| `--arms` / `--legs` | `keep` / `fold` / `hide`。形状 0〜8 で選べる |
| `--appendages` | 耳と尾を `keep`（形を保つ）/ `integrate`（形状へ統合）。形状 0〜8 で選べる |
| `--size` | 形状全体の大きさ（0.2〜2） |
| `--scene` / `--style` / `--extra` | 場面・画風・追加の作画指定 |
| `--ref` | 追加の参照画像。`references/` か `outputs/` の中のファイルに限る |
| `--no-refs` | 公式画像を添付しない（Stage 2 の観察もできなくなる） |
| `--iterate-from` / `--revisions` | 改稿の起点（実行フォルダー名か `outputs/` の中の画像）と、変える点（`;` 区切り） |
| `--provider` | 画像モデルの提供元。`gemini`（既定）/ `openai` |
| `--count` / `--rough-count` | 完成画像の枚数（1〜4）/ ラフの枚数（1〜5） |
| `--quick` | 検査なしで 1 回だけ生成する |
| `--skip-canva` | Canva 仕上げを省く |
| `--height-cm` | 身長を上書きする（DB に身長が無いキャラクター向け） |

### 環境変数

`.env`（[.env.example](../.env.example)）から読む。

- 画像モデル: `GEMINI_API_KEY` と `GEMINI_IMAGE_MODEL`（未設定なら `GEMINI_REFERENCE_MODEL`）、`OPENAI_API_KEY` と `OPENAI_IMAGE_MODEL`。
- 検査: `OPENAI_API_KEY` と `GPT_MODEL`（既定 `gpt-4o`）。
- Canva: `SHAPECHANGE_CANVA_ACCESS_TOKEN`（取得と更新は下の「Canva の連携」）。GeneratorsAI の `CANVA_ACCESS_TOKEN` は読まない。Canva のリフレッシュトークンは使うたびに入れ替わるので、連携を共有すると片方が失効する。

### Canva の連携

Stage 5 の Canva 仕上げは、ShapeChangerAI 用に作った Canva の連携で行う。トークンの取得と更新の方法は GeneratorsAI と同じ（OAuth 2.0 の PKCE とリフレッシュトークン）で、環境変数は `SHAPECHANGE_CANVA_*` に分けている。連携はまだ作っていないので、この手順は実際の Canva API では確かめていない（トークンのやり取りは偽物でテストしている）。

1. Canva の Developer Portal で連携を作る（[Canva Connect](https://www.canva.dev/docs/connect/)）。Redirect URL に `http://127.0.0.1:3001/oauth/redirect` を登録し、スコープは `asset:read`・`asset:write`・`design:content:read`・`design:content:write`・`design:meta:read` を有効にする。
2. 連携の Client ID と Client Secret を、`.env` の `SHAPECHANGE_CANVA_CLIENT_ID` と `SHAPECHANGE_CANVA_CLIENT_SECRET` に入れる。
3. トークンを取得する。表示された URL をブラウザで開いて許可すると、`.env` の `SHAPECHANGE_CANVA_ACCESS_TOKEN` と `SHAPECHANGE_CANVA_REFRESH_TOKEN` が書き込まれる。トークンの値は画面に出ない。

```sh
python -m src.tools.refresh_canva_token                        # 初回: ブラウザで許可して取得する
python -m src.tools.refresh_canva_token --use-refresh-token    # 2 回目以降: ブラウザなしで更新する
```

アクセストークンは短い時間で失効する。失効していると Canva 仕上げだけが省かれ、完成画像は Canva を通さないものになる（理由は `run_meta.json` の Stage 5 の記録に残る）。そのときは `--use-refresh-token` か、コネクタの `shapechange_refresh_canva_token` で更新してから生成し直す。コネクタのツールは、新しいトークンを `.env`（あれば）と Secret Manager（使えれば）へ保存し、保存できた場所を結果の `saved_to` に返す。Secret Manager では、新しい版を足したあとに 1 つ前の版を消す（入れ替わる前のトークンはもう使えず、版は残っているだけで課金されるため）。

リフレッシュトークンは 1 回使うと新しいものに入れ替わる。PC の `.env` と Secret Manager が同じトークンを持っているとき、片方で更新すると、もう片方のリフレッシュトークンは使えなくなる。使えなくなったら、PC で引数なしの取得からやり直し、Cloud Run で使う分は登録し直す（[docs/deploy-cloud-run.md](../docs/deploy-cloud-run.md) の「Canva の連携」）。

## 出力

```text
outputs/{YYYYMMDD_HHMMSS}_{provider}_mode{NN}_{作品}-{識別子}/
├── prompt.txt                      実際に使ったプロンプト（Stage 2 の観察を足したもの）
├── run_meta.json                   日時・モデル・対象・形状と寸法・参照元・各 Stage の結果・元の引数
├── notes.md                        確認結果と再試行で見直す点（手書き用）
├── stage2_reference/observation.json   観察結果、または失敗理由と回答
├── stage3_rough/rough_NN.png
├── stage4_correct/analysis_log.json    ラフごとの指摘・数えた本数・処置
├── stage4_correct/rough_NN_corrected_01.png / rough_NN_regen_01.png
└── stage5_final/mode04_….png       完成画像（Canva 仕上げは stage5_final/canva/）
```

合同生成は `…_joint_{対象}_{対象}/` に、キャラクターごとの `char_{対象}/stage2〜4` と、全員を描いた `stage5_final/joint_01.png` を置く。`--quick` は実行フォルダー直下に画像を置く。3 つの記録は生成 API を呼ぶ前に作るので、失敗時もプロンプトと条件が残る。`outputs/` は Git の追跡対象外。

## コネクタ（MCP サーバー）

```sh
pip install -r requirements-mcp.txt
python -m src.mcp_server            # ローカル（stdio）
```

Claude Desktop などに登録する例（パスは環境に合わせる）:

```json
{
  "mcpServers": {
    "shapechange": {
      "command": "py",
      "args": ["-m", "src.mcp_server"],
      "cwd": "C:\\Visual Studio Code UserFile\\100BeautiesLab_ShapeChangerAI",
      "env": { "MCP_TRANSPORT": "stdio", "OUTPUT_SINK": "local", "PYTHONUTF8": "1" }
    }
  }
}
```

| ツール | 内容 |
| --- | --- |
| `shapechange_generate_character` / `shapechange_generate_joint` | 単体・合同の生成。ジョブを登録して `job_id` を返す |
| `shapechange_generate_from_natural` | 自然文の依頼から生成。読み取り結果は結果の `interpretation` に入る |
| `shapechange_iterate` | 以前の実行と同じ指定で、その完成画像を起点に改稿する |
| `shapechange_job_status` | 進捗と完成画像のリンク。`awaiting_confirmation` なら利用者に確認する |
| `shapechange_answer_reference_warning` | Stage 2 の観察失敗に対する続行／中止の回答（無回答は 30 分で中止） |
| `shapechange_list_runs` / `shapechange_get_run_logs` | 実行の一覧と、プロンプト・検査ログ・中間画像 |
| `shapechange_list_gcs_logs` / `shapechange_get_gcs_image` | GCS に残っている中間画像の一覧（署名付き URL 付き）と、1 件の取得（base64） |
| `shapechange_refresh_canva_token` | Canva のアクセストークンの更新（上の「Canva の連携」） |
| `shapechange_list_modes` / `shapechange_list_characters` | 指定できる形状とキャラクター |

完成画像は `OUTPUT_SINK`（`local` / `drive` / `gcs`）へ届ける。Drive では `DRIVE_FOLDER_ID` の直下に `shapechange_{実行名}__…` の名前で置く。中間画像は Drive へ上げず、`GCS_BUCKET` があれば GCS、無ければサーバー内に残す。ジョブはメモリー上にあり、サーバーを再起動すると消える（実行の記録は `outputs/` に残る）。

GCS に置いた中間画像は、`shapechange_list_gcs_logs` で後から一覧できる。バケットを直接見るので、サーバーが再起動して `outputs/` が消えたあとも使える。GeneratorsAI の `numbertales_list_gcs_logs`・`numbertales_get_gcs_image` と同じ作りで、違いは 2 つ。取得できる画像を `GCS_PREFIX`（既定 `shapechange`）の下に限っていることと、一覧の既定の件数が 50 件であること（GeneratorsAI は 300 件）。

VM では、GeneratorsAI と同じく、使うときだけ起動して SSH 越しの stdio でつなぐ。配置と更新の手順は [docs/deploy-vm.md](../docs/deploy-vm.md)。

claude.ai のコネクタとして使う公開の配置は Cloud Run にある。設定・配置・登録の手順は [docs/deploy-cloud-run.md](../docs/deploy-cloud-run.md)。

HTTP で公開する場合は `MCP_TRANSPORT=streamable-http` と `MCP_ISSUER_URL` を設定する。認証は自動承認の OAuth で、利用者の本人確認はしない。VM に常駐させて公開する形は見送った。

VM は、公開用のシェーダーパッケージをサブモジュールとして取得する。Cloud Run のイメージには、次のコマンドが空のフォルダーへ書き出すシェーダーの 2 ファイルだけを入れ、`SHAPECHANGE_REPO_PATH` で置き場所を指す（[deploy/deploy_cloud_run.py](../deploy/deploy_cloud_run.py) が行う）。

```sh
python -m src.shape.vm_bundle outputs/vm-shape-data
```

## 構成

| ファイル | 役割 |
| --- | --- |
| `pipeline/shape_pipeline.py` | CLI と実行本体（計画 → 5 段階 → 記録） |
| `pipeline/prompt_builder.py` | プロンプトの組み立て（単体・合同） |
| `pipeline/design_reference.py` | Stage 2: 公式設定画の観察と確認待ち |
| `pipeline/correction_generator.py` | Stage 4: 検査と修正 |
| `pipeline/final_generator.py` | Stage 5: 清書と Canva 仕上げ |
| `pipeline/natural_parser.py` | 自然文の依頼の読み取り |
| `shape/vrc_source.py` / `shape/modes.py` | シェーダーからの寸法の読み取り、形状仕様と体格の計算 |
| `shape/vm_bundle.py` | Cloud Run のイメージへ渡すシェーダー 2 ファイルの書き出し |
| `utils/dataset.py` | manifest の読み込み、AI 利用ゲート、キャラクター検索、参照画像の選定 |
| `gemini/generate.py` / `openai/generate.py` / `openai/vision.py` / `canva/generate.py` | 外部 API の呼び出し |
| `tools/refresh_canva_token.py` | Canva の連携のトークンの取得・更新と、Secret Manager への登録 |
| `mcp_server/` | MCP サーバー。`tools.py` が中身、`server.py` が MCP への登録。`jobs.py` / `auth.py` / `output_sink.py` は GeneratorsAI から移したもの |
| `utils/run_log.py` / `utils/paths.py` / `utils/image_io.py` / `utils/provider.py` | 記録・実行フォルダー・画像の保存・共通の型 |

## テスト

```sh
python tests/test_shape_source.py    # シェーダーからの寸法の読み取り
python tests/test_dataset_gate.py    # キャラクター検索と AI 利用ゲート、衣装と画像の除外
python tests/test_pipeline.py        # 5 段階・合同・改稿・自然文・記録
python tests/test_providers.py       # 外部 API の呼び出し方（SDK は偽物に差し替え）
python tests/test_mcp_tools.py       # MCP ツールの中身（MCP SDK は不要）
python tests/test_canva_token.py     # Canva の連携のトークンの取得・更新（Canva と GCP は偽物に差し替え）
python tests/test_paths.py           # 実行フォルダーの作成
```

生成 API は呼ばない。サブモジュールを更新した後はすべて実行する。
