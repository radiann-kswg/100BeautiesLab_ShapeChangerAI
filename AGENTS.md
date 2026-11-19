# AGENTS.md — 100BeautiesLab_ShapeChangerAI

このファイルは、リポジトリ全体に適用する**共通運用の正本（SSOT）**です。役柄と口調の正本は [SOUL.md](SOUL.md) です。エージェント固有の設定書には、読み込み経路とツール固有の事項だけを置きます。

## 応答を始める前に

**依頼の内容に答え始める前に、ルートの [SOUL.md](SOUL.md) を開いて読み、扇 四春として応答する。** すでに読み込まれていれば、その内容を使う。会話の要約や引き継ぎで設定が手元になければ、次の応答前に読み直す。

雑談、創作相談、技術作業、途中経過と完了の報告まで、ユーザー向けの説明に適用する。ユーザーがロールプレイの停止を求めたら通常の応答に戻す。成果物は依頼された文体と形式で作る。

## エージェント設定の配布構成

| エージェント | 共通運用の読み込み経路 |
| --- | --- |
| Codex | このリポジトリを開くとルートの `AGENTS.md` を自動で読み込む。画像生成・編集や生成環境の確認では [CODEX.md](CODEX.md) も読む |
| Claude Code / Claude Desktop の Code | [CLAUDE.md](CLAUDE.md) のインポートで `AGENTS.md` と `SOUL.md` を読み込む |
| Claude Desktop の Cowork | [CLAUDE.Cowork.md](CLAUDE.Cowork.md) のプロジェクト指示で `AGENTS.md` を読む |
| GitHub Copilot | [.github/copilot-instructions.md](.github/copilot-instructions.md) から `AGENTS.md` を読む |
| その他のエージェント | リポジトリのルートを開き、`AGENTS.md` を作業指示として読む |

Claude Desktop のプロジェクト指示を更新するときは、Codex にも適用する作業ルールをこのファイルに反映する。

## 制作と確認

現在の依頼から作業範囲を判断する。原典や添付資料は、人物設定・作画条件・権利条件を確認する資料として扱う。

1. 対象キャラクター、元の姿、依頼された形状を確認する。髪や瞳の色、衣装、耳や尻尾の本数など、維持する特徴を原設定から拾う。[著作物の扱い](#著作物の扱い) に沿って入力素材を選ぶ。
2. 仕上がりの輪郭、縦横比と厚み、顔の位置、手足・衣装・付属物の見え方をプロンプトに落とす。途中経過を描く場合は、元の姿・途中・変形完了で何が変わるかを指定する。
3. OpenAI の GPT Image または Gemini の Nano Banana で生成・編集する。完成画像の仕上げと書き出しには Canva も使える（画像の生成には使わない）。結果を元設定と見比べ、輪郭とキャラクターの識別に必要な特徴を確認する。再生成では変えた点を記録し、別の実行フォルダーに保存する。

GeneratorsAI の既存生成機能を実行する場合は、同リポジトリの `.agents/skills/numbertales-imagegen/SKILL.md` を先に読む。参考にする実行記録の仕様は、同リポジトリの `docs/output-and-logs.md` にある。

## 生成パイプライン

`src/` のパイプラインは、上の手順の入力選定・プロンプト・生成・検査・記録を 1 コマンドで行う。既定は GeneratorsAI と同じ区切りの 5 段階（プロンプト → 公式設定画の観察 → ラフ → 検査と修正 → 清書）。オプションと構成は [src/README.md](src/README.md)、合意した要件と保留事項は [docs/requirements-5stage-connector.md](docs/requirements-5stage-connector.md) にある。

```sh
pip install -r requirements.txt
python -m src.pipeline.shape_pipeline --list-modes
python -m src.pipeline.shape_pipeline --badge 57 --mode 4 --dry-run
python -m src.pipeline.shape_pipeline --badge 57 --mode 4            # 5 段階
python -m src.pipeline.shape_pipeline --badge 57 --mode 4 --quick    # 検査なしで 1 回だけ
python -m src.mcp_server                                             # コネクタ（MCP サーバー）
```

- PowerShell で `python` が Microsoft Store のエイリアスに当たって起動しない場合は、`python` を `py` に読み替える。
- 生成は課金を伴う。先に `--dry-run` でプロンプトと参照画像を確認し、依頼者に示してから本番実行する。5 段階は 1 回の実行で画像生成が最大 7 回ほどかかる。
- Stage 2 の画像観察が失敗したときは、利用者が続行を明示した場合だけ先へ進む。無回答は中止として扱う。
- Canva は、このリポジトリ用の連携（`SHAPECHANGE_CANVA_ACCESS_TOKEN`）だけを使う。GeneratorsAI の `CANVA_*` を流用しない。
- キャラクターは manifest の `ai_training.allowed` が true のレコードだけを受け付ける。追加の参照画像（`--ref`）は `references/` か `outputs/` に置いたファイルに限る。
- 形状の寸法は実行のたびに `VRCShader_ShapeChange` のシェーダーから読む。サブモジュールの更新後に読み取りが止まった場合は、`src/shape/vrc_source.py` をシェーダーの記述に合わせる。輪郭や顔の位置の記述は `src/shape/mode_descriptions.json` で直す。
- `VRCShader_ShapeChange/` は公開用のシェーダーパッケージで、モデルや検証用のデータを含まない。VM はこのサブモジュールをそのまま取得する。Cloud Run のイメージには、`python -m src.shape.vm_bundle <空のフォルダー>` が書き出すシェーダーの 2 ファイルだけを入れる。シェーダーの開発用リポジトリ（`VRCShader_ShapeChange-Develop`）の作業コピーにはモデルや検証用のデータがあるので、VM にもイメージにも置かない。
- `src/` を変更したとき、サブモジュールを更新したときは `tests/` のテストを実行する（生成 API は呼ばない）。

## 著作物の扱い

- AI に渡す素材は、出典・作者・権利条件と、使用するサービスでの対象用途を確認して選ぶ。ラジアン（柏木主税）の作品に含まれる第三者の素材にも同じ確認を行う。
- ラジアン（柏木主税）以外の著作物は、AI 学習・追加学習や二次生成（画像参照・画像編集を含む）に使わない。例外は、その用途を権利者が明示的に許諾し、利用規約を満たし、ユーザーが今回の依頼で使用を指定した場合に限る。許諾の範囲が確認できない素材は AI に渡さない。
- CreationsDB の収録キャラクターは `AI_Optout` と CreationsAI の `ai_training.allowed` を照合する。拒否設定のあるレコードと参照素材は学習・生成入力から除外する。`ai_training.allowed: true` の場合も、上記の権利条件とサービスへの入力可否を確認する。
- Unity の作例を参考にするときは、球体・平面などの一般的な形状、姿勢、部位配置を自分の言葉で記述する。第三者のモデル・テクスチャ・レンダーはローカルでの閲覧にとどめる。

## 出力と記録

手元の素材は `references/`、生成画像と実行記録は `outputs/<run-id>/` に置く。どちらも Git の追跡対象外。実行ごとに以下を残す（パイプラインは自動で作る）。

- `prompt.txt` に実際に使ったプロンプト
- `run_meta.json` に実行日時・サービス・モデル・対象キャラクター・参照元
- `notes.md` に確認結果と再試行時に見直す点

## Git ブランチ運用

GeneratorsAI と同じ分け方にする。開発も公開も、このリポジトリ `100BeautiesLab_ShapeChangerAI` で行う。2026-11-19 に、非公開だった `100BeautiesLab_ShapeChangerAI-Develop` から開発を移した（経緯は [docs/publish.md](docs/publish.md)）。

| ブランチ | 役割 |
| --- | --- |
| `main` | 公開とデプロイの基準。エージェントは直接コミット・push しない |
| `develop` | 開発の統合先。実装、プロンプトや形状の記述の調整、ドキュメントの更新、サブモジュールの追従コミットはここで進める |

- `develop` から `main` への反映は Pull Request で行う。`main` へ直接マージ・push しない。
- 長くかかる作業は `develop` から `feature/…` や `fix/…` の作業ブランチを切り、終わったら `develop` へ戻す。
- エージェントは `develop` か作業ブランチにコミットする。push と Pull Request の作成は、ユーザーが依頼したときに行う。
- `develop` も作業ブランチも、push した時点で公開される。Cloud Run の URL や秘密の値など、公開の場に書かない値はコミットに入れない（[docs/publish.md](docs/publish.md)）。

## サブモジュール運用

| パス | 用途 |
| --- | --- |
| `_creations-ai/` | CreationsAI。キャラクターと AI 利用条件を `ai-dataset/manifest.jsonl` で照合する |
| `_creations-ai/creations-db/` | CreationsAI が固定する CreationsDB。キャラクター原典は `data/` にある |
| `VRCShader_ShapeChange/` | 形状変化シェーダーの公開用パッケージ（[VRCShader_ShapeChange](https://github.com/radiann-kswg/VRCShader_ShapeChange)）。形状・部位配置の参照には `README.md` を使う。寸法はルートの `ShapeChange.hlsl` と `ShapeChangeProperties.lilblock` からパイプラインが読む |

初回取得と親リポジトリの切り替え後は、ルートで次を実行する。サブモジュールはどれも公開リポジトリ。

```sh
git submodule update --init --recursive
git submodule status --recursive
```

普段は記録されたコミットを使う。意図して更新する場合は [`.gitmodules`](.gitmodules) の追跡ブランチの差分を確認し、親側の Git リンクを更新する。CreationsDB の版は CreationsAI 側の Git リンクに従う。上流の資料・モデルは取得した各サブモジュールで参照し、親リポジトリには Git リンクを記録する。
