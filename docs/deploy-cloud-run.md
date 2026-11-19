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
| モデル | VM の `.env` と同じ値を [deploy/deploy_cloud_run.py](../deploy/deploy_cloud_run.py) の `ENV_VARS` に書いている |

GeneratorsAI との違いは 2 つ。GitHub Actions からの自動デプロイは無く、PC からスクリプトで配置する。イメージは専用の Artifact Registry ではなく、`gcloud run deploy --source` が使う `cloud-run-source-deploy` に入る。

## 公開範囲について

自動承認の OAuth は利用者の本人確認をしない。URL を知っていれば、誰でも課金を伴う生成を呼べる（GeneratorsAI のコネクタも同じ）。URL は公開の場に書かない。このリポジトリの内容は公開リポジトリへ写すので、実際の URL とプロジェクト番号はここにも書かない。止める場合:

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

## Canva の連携

Canva 仕上げを Cloud Run でも行う場合の手順。連携の作成とトークンの取得は [src/README.md](../src/README.md) の「Canva の連携」。連携はまだ作っていないので、この手順は実際には通していない。

1. PC でトークンを取得したあと、`.env` の `SHAPECHANGE_CANVA_*` 4 件を Secret Manager へ登録する。無いシークレットは作り、有るものには新しい版を足す。値は標準入力で渡すので、画面にもコマンドラインにも出ない。

   ```sh
   python -m src.tools.refresh_canva_token --push-secrets claude-radiannkswg
   ```

2. 上の「配置と更新」のコマンドで配置し直す。4 件がそろっていれば「Canva の連携（SHAPECHANGE_CANVA_*）を渡します。」と表示される。
3. Cloud Run のサービスアカウントに、4 件を読む権限（`roles/secretmanager.secretAccessor`）と、トークンの 2 件へ版を足す権限（`roles/secretmanager.secretVersionAdder`）があることを確かめる。版を足す権限が無いと、`shapechange_refresh_canva_token` で更新したトークンを保存できず、結果の `saved_to` が空になる。

Cloud Run は、インスタンスの起動時に Secret Manager の最新版を読む。アクセストークンは短い時間で失効するので、Canva 仕上げを使う生成の前に `shapechange_refresh_canva_token` で更新する。

## コネクタの登録

1. claude.ai の「Customize」→「Connectors」で「Add custom connector」を選ぶ。
2. URL に、上の表の URL の末尾へ `/mcp` を付けたものを入れて追加する。
3. 会話の「+」→「Connectors」で有効にする。`shapechange_` で始まるツールが 13 件見える。

2026-11-18 の配置では、登録と同じ手順（OAuth の登録・認可・トークン取得）を PC から行い、ツール一覧・形状一覧・キャラクター一覧が返ることまで確かめた。Cloud Run からの生成と Drive 保存、claude.ai への登録は未確認。

## 制約

- ジョブ・OAuth のトークン・`outputs/` はインスタンスのメモリー上にある。しばらく使わないとインスタンスが止まり、すべて消える。止まったあとはコネクタの再接続を求められることがある。
- 改稿（`shapechange_iterate`）と実行ログの照会は、起点の実行が同じインスタンスに残っている間だけ使える。完成画像は Drive、中間画像は GCS に残る。GCS の中間画像は、インスタンスが止まったあとも `shapechange_list_gcs_logs` で一覧できる。
- Canva 仕上げは、ShapeChangerAI 用の連携を Secret Manager へ登録するまで行わない（連携はまだ作っていない）。
