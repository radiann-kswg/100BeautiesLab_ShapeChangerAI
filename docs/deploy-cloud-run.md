# Cloud Run への配置（公開コネクタ）

claude.ai の Web やスマホからコネクタとして使うための配置。GeneratorsAI のコネクタ（Cloud Run の `generators-ai`）と同じ設定にしている。決めた経緯は [requirements-5stage-connector.md](requirements-5stage-connector.md)。VM を SSH で使う形は [deploy-vm.md](deploy-vm.md)。

## 設定

| 項目 | 値 |
| --- | --- |
| サービス | `shapechanger-ai`（プロジェクト `claude-radiannkswg`、リージョン `asia-northeast1`） |
| URL | `https://shapechanger-ai-<プロジェクト番号>.asia-northeast1.run.app`（コネクタに登録するのは末尾に `/mcp` を付けたもの）。プロジェクト番号は `gcloud projects describe claude-radiannkswg --format="value(projectNumber)"` で確かめる |
| 公開範囲 | 認証なしで呼べる（`allUsers` に `roles/run.invoker`）。MCP の認証は自動承認の OAuth |
| インスタンス | 最小 0・最大 1、メモリー 1Gi、CPU は常時割り当て、タイムアウト 3600 秒 |
| 秘密の値 | Secret Manager の `GEMINI_API_KEY`・`OPENAI_API_KEY`・`DRIVE_*`（最新版）。Canva の連携（`SHAPECHANGE_CANVA_*` の 4 件）は、4 件とも Secret Manager にあるときだけ渡す（下の「Canva の連携」） |
| 完成画像 | Drive（GeneratorsAI と同じフォルダー、名前は `shapechange_` で始まる） |
| 中間画像 | GCS の `generators-ai-outputs` バケット、接頭辞 `shapechange/`。署名付き URL（7 日間有効）で返す |
| 入力画像 | 同じバケットの `shapechange/uploads/<日付>/`。`shapechange_create_upload_slot` の署名付き PUT URL（15 分有効）で受け取る。7 日で消す（下の「入力画像の置き場」） |
| 改稿の起点 | 同じバケットの `shapechange/runs/<run_id>/`。実行の `run_meta.json` と完成画像を置く |
| モデル | VM の `.env` と同じ値を [deploy/deploy_cloud_run.py](../deploy/deploy_cloud_run.py) の `ENV_VARS` に書いている |

GeneratorsAI との違いは 2 つ。GitHub Actions からの自動デプロイは無く、PC からスクリプトで配置する。イメージは専用の Artifact Registry ではなく、`gcloud run deploy --source` が使う `cloud-run-source-deploy` に入る。

## 公開範囲について

自動承認の OAuth は利用者の本人確認をしない。URL を知っていれば、誰でも課金を伴う生成を呼べる（GeneratorsAI のコネクタも同じ）。GCS に残った中間画像の一覧と取得、Canva のトークンの更新も、同じように呼べる。URL は公開の場に書かない。このリポジトリの内容は公開リポジトリへ写すので、実際の URL とプロジェクト番号はここにも書かない。止める場合:

```sh
# 公開をやめる（サービスは残る。コネクタからは呼べなくなる）
gcloud run services remove-iam-policy-binding shapechanger-ai --region=asia-northeast1 --member=allUsers --role=roles/run.invoker
# サービスごと消す
gcloud run services delete shapechanger-ai --region=asia-northeast1
```

## 配置と更新

PC のリポジトリのルートで実行する。コードの更新も、シェーダーやキャラクターデータの更新も、同じコマンドで配置し直す。

```sh
python deploy/deploy_cloud_run.py --stage-only   # 送る内容の確認だけ（GCP には触れない）
python deploy/deploy_cloud_run.py                # ビルドして配置する
```

スクリプトはリポジトリのルートをそのまま送らず、一時フォルダーに次のものだけを組み立てて Cloud Build へ送る。

- コミット済みのコード（`HEAD` の `Dockerfile`・`requirements*.txt`・`src/`）。未コミットの変更は入らない
- キャラクターデータ（`_creations-ai/ai-dataset` と `_creations-ai/creations-db/data`）。tar にまとめて送る
- 形状データ。`vm_bundle` が書き出すシェーダーの 2 ファイルだけ

`VRCShader_ShapeChange` のフォルダーや `.env` が混ざった場合、形状データが 2 ファイルでない場合は、送る前に止まる。送った内容は、プロジェクトの Cloud Build 用バケット（`run-sources-claude-radiannkswg-asia-northeast1`）に残る。

## 入力画像の置き場

入力画像は、バケットのライフサイクルで 7 日後に消す。この設定はバケットに 1 回入れればよく、配置のたびには要らない。2026-11-20 の時点では、まだ入れていない。

`generators-ai-outputs` は GeneratorsAI と共用で、`--lifecycle-file` はバケットの設定を丸ごと置き換える。先にいまの設定を読み、既存のルールを残したまま、次のルールを足す。

```sh
gcloud storage buckets describe gs://generators-ai-outputs --format="json(lifecycle_config)"
```

```json
{"action": {"type": "Delete"}, "condition": {"age": 7, "matchesPrefix": ["shapechange/uploads/"]}}
```

```sh
gcloud storage buckets update gs://generators-ai-outputs --lifecycle-file=<ルールを足した JSON>
```

Cloud Run のサービスアカウントには、読み取り用の署名付き URL と同じ権限（オブジェクトの作成と読み取り、自分自身の `iam.serviceAccounts.signBlob`）があれば足りる。署名付き PUT URL には、形式（`Content-Type`）と大きさの上限（`x-goog-content-length-range`、10MB）を含めている。実際の GCS へのアップロードは、配置してから確かめる（2026-11-20 の時点では、偽の GCS でしか確かめていない）。

`shapechange/runs/` には消す設定を入れていない。完成画像が Drive と GCS の両方に残り続けるので、期間を決めて同じ形のルールを足すかどうかは、使ってみてから決める。

## Canva の連携

Canva 仕上げを Cloud Run でも行う場合の手順。連携の作成とトークンの取得は [src/README.md](../src/README.md) の「Canva の連携」。連携はまだ作っていないので、この手順は実際には通していない。

1. PC でトークンを取得したあと、`.env` の `SHAPECHANGE_CANVA_*` 4 件を Secret Manager へ登録する。無いシークレットは作り、有るものには新しい版を足す。値は標準入力で渡すので、画面にもコマンドラインにも出ない。

   ```sh
   python -m src.tools.refresh_canva_token --push-secrets claude-radiannkswg
   ```

2. 上の「配置と更新」のコマンドで配置し直す。4 件がそろっていれば「Canva の連携（SHAPECHANGE_CANVA_*）を渡します。」と表示される。
3. Cloud Run のサービスアカウントに、4 件を読む権限（`roles/secretmanager.secretAccessor`）と、トークンの 2 件の版を足して消す権限（`roles/secretmanager.secretVersionManager`）があることを確かめる。版を足せないと、`shapechange_refresh_canva_token` で更新したトークンを保存できず、結果の `saved_to` が空になる。版を消せないと、更新のたびに古い版が残り、その数だけ Secret Manager の料金がかかる。

Cloud Run は、インスタンスの起動時に Secret Manager の最新版を読む。アクセストークンは短い時間で失効するので、Canva 仕上げを使う生成の前に `shapechange_refresh_canva_token` で更新する。

## コネクタの登録

1. claude.ai の「Customize」→「Connectors」で「Add custom connector」を選ぶ。
2. URL に、上の表の URL の末尾へ `/mcp` を付けたものを入れて追加する。
3. 会話の「+」→「Connectors」で有効にする。`shapechange_` で始まるツールが 14 件見える。

2026-11-18 の配置では、登録と同じ手順（OAuth の登録・認可・トークン取得）を PC から行い、ツール一覧・形状一覧・キャラクター一覧が返ることまで確かめた。Cloud Run からの生成と Drive 保存、claude.ai への登録は未確認。

## 制約

- ジョブ・OAuth のトークン・`outputs/` はインスタンスのメモリー上にある。しばらく使わないとインスタンスが止まり、すべて消える。止まったあとはコネクタの再接続を求められることがある。
- 改稿（`shapechange_iterate`）は、インスタンスが止まったあとも、GCS の `shapechange/runs/` に残した `run_meta.json` と完成画像を戻して続けられる。`shapechange_list_runs` には `location: gcs` として並ぶ。この仕組みを入れる前（2026-11-20 より前の配置）の実行は、GCS に残っていないので起点にできない。
- 実行ログの照会（`shapechange_get_run_logs`）で、インスタンスが止まったあとに返せるのは `run_meta.json` だけ。プロンプト・観察・検査ログは戻らない。完成画像は Drive、中間画像は GCS に残り、GCS の中間画像は `shapechange_list_gcs_logs` で一覧できる。
- Canva 仕上げは、ShapeChangerAI 用の連携を Secret Manager へ登録するまで行わない（連携はまだ作っていない）。
