# Cloud Run への配置（公開コネクタ）

claude.ai の Web やスマホからコネクタとして使うための配置。GeneratorsAI のコネクタ（Cloud Run の `generators-ai`）と同じ設定にしている。決めた経緯は [requirements-5stage-connector.md](requirements-5stage-connector.md)。VM を SSH で使う形は [deploy-vm.md](deploy-vm.md)。

## 設定

| 項目 | 値 |
| --- | --- |
| サービス | `shapechanger-ai`（リージョン `asia-northeast1`）。プロジェクトは、環境変数 `DEPLOY_GCP_PROJECT` か gcloud の既定（`gcloud config get-value project`） |
| URL | `https://shapechanger-ai-<プロジェクト番号>.asia-northeast1.run.app`（コネクタに登録するのは末尾に `/mcp` を付けたもの）。プロジェクト番号は `gcloud projects describe <PROJECT_ID> --format="value(projectNumber)"` で確かめる |
| 公開範囲 | 認証なしで呼べる（`allUsers` に `roles/run.invoker`）。MCP の認証は OAuth で、接続のたびに合言葉の確認画面を通す（下の「公開範囲について」） |
| インスタンス | 最小 0・最大 1、メモリー 1Gi、CPU は常時割り当て、タイムアウト 3600 秒 |
| 秘密の値 | Secret Manager の `MCP_AUTH_PASSPHRASE`（コネクタの合言葉。GeneratorsAI と同じシークレット）・`GEMINI_API_KEY`・`OPENAI_API_KEY`・`DRIVE_*`（最新版）。Canva の連携（`SHAPECHANGE_CANVA_*` の 4 件）は、4 件とも Secret Manager にあるときだけ渡す（下の「Canva の連携」） |
| 完成画像 | Drive（GeneratorsAI と同じフォルダー、名前は `shapechange_` で始まる） |
| 中間画像 | GCS のバケット（GeneratorsAI と共用）、接頭辞 `shapechange/`。バケット名は、環境変数 `DEPLOY_GCS_BUCKET` か、配置済みのサービスの設定を引き継ぐ。署名付き URL（7 日間有効）で返す |
| 入力画像 | 同じバケットの `shapechange/uploads/<日付>/`。`shapechange_create_upload_slot` の署名付き PUT URL（15 分有効）で受け取る。7 日で消す（下の「入力画像の置き場」） |
| 改稿の起点 | 同じバケットの `shapechange/runs/<run_id>/`。実行の `run_meta.json` と完成画像を置く |
| モデル | VM の `.env` と同じ値を [deploy/deploy_cloud_run.py](../deploy/deploy_cloud_run.py) の `ENV_VARS` に書いている |

配置のしかたは 2 つある。`main` に変更が入ると GitHub Actions が配置する（下の「GitHub Actions からの配置」）。PC からスクリプトで配置することもできる（下の「配置と更新」）。どちらも、送る内容の組み立てと配置の設定は [deploy/deploy_cloud_run.py](../deploy/deploy_cloud_run.py) が決める。イメージの置き場は違い、GitHub Actions は GeneratorsAI と同じ Artifact Registry のリポジトリ（イメージ名 `shapechanger-mcp`）、PC からの配置は `gcloud run deploy --source` が使う `cloud-run-source-deploy` に入る。

## 公開範囲について

コネクタの接続（OAuth の認可）は、合言葉の確認画面（`/consent`）を通す。合言葉は Secret Manager の `MCP_AUTH_PASSPHRASE` で、GeneratorsAI のコネクタと同じものを使う。合言葉が未設定のサーバーは接続をすべて断り、`MCP_ISSUER_URL` が無いサーバーは起動を断る。合言葉は 1 件の接続で 5 回まで、全体で 10 分に 10 回まちがえると、しばらく受け付けない。

合言葉を知っている人は、課金を伴う生成、GCS に残った中間画像の一覧と取得、Canva のトークンの更新を呼べる。利用者ごとの本人確認はしない。合言葉を変えるときは、Secret Manager に新しい版を足して配置し直す。サインインの状態は GCS に残るが（下の「制約」）、保存する名前は合言葉を鍵にしたハッシュなので、合言葉を変えると発行済みのトークンはどれも通らなくなり、全員が再サインインになる。

```sh
# 合言葉を登録する（初回は create、変えるときは versions add）。値は標準入力で渡し、画面にもコマンドラインにも出さない
gcloud secrets create MCP_AUTH_PASSPHRASE --data-file=-
gcloud secrets versions add MCP_AUTH_PASSPHRASE --data-file=-
```

2026-11-21 までは自動承認の OAuth で、URL を知っていれば誰でも呼べた。このリポジトリは公開なので、実際の URL・プロジェクト ID・プロジェクト番号・バケット名はここにも書かない。止める場合:

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

`VRCShader_ShapeChange` のフォルダーや `.env` が混ざった場合、形状データが 2 ファイルでない場合は、送る前に止まる。送った内容は、プロジェクトの Cloud Build 用バケット（`run-sources-<PROJECT_ID>-asia-northeast1`）に残る。

## GitHub Actions からの配置

[.github/workflows/deploy-mcp-server.yml](../.github/workflows/deploy-mcp-server.yml) が、`main` への push（`src/`・`requirements*.txt`・`Dockerfile`・`deploy/deploy_cloud_run.py`・ワークフロー自身・2 つのサブモジュールの変更）で動く。GitHub の画面から手動でも実行できる。

1. テストを流す（生成 API は呼ばない）。失敗したら配置しない。
2. `python deploy/deploy_cloud_run.py --stage-dir <フォルダー>` で、PC からの配置と同じ内容を組み立てる。
3. そのフォルダーで Docker イメージをビルドし、Artifact Registry へ送る。
4. `python deploy/deploy_cloud_run.py --image <イメージ>` で配置する。環境変数・シークレット・インスタンスの設定は、PC からの配置と同じ。

コネクタの URL は公開のログに出さない（配置のときの gcloud の出力は表示しない）。配置しても、サインインの状態は GCS に残るので、再サインインは要らない（下の「制約」）。

準備（初回だけ。値はこのリポジトリに書かない）:

| 場所 | 必要なもの |
| --- | --- |
| リポジトリのシークレット | `GCP_PROJECT_ID`、`GCP_WIF_PROVIDER`（Workload Identity のプロバイダーのリソース名）、`GCP_SERVICE_ACCOUNT`（配置に使うサービスアカウントのメール）、`MCP_ISSUER_URL`（このサービスの URL。配置済みのサービスの環境変数 `MCP_ISSUER_URL` と同じ値）、`GCS_BUCKET`（中間画像のバケット名） |
| Workload Identity のプロバイダー | 属性の条件（`attribute-condition`）で、このリポジトリからの実行を許す。GeneratorsAI と同じプロバイダーを使うなら、条件を 2 つのリポジトリの並びにする |
| サービスアカウント | このリポジトリの `principalSet`（`…/attribute.repository/<オーナー>/<リポジトリ>`）に `roles/iam.workloadIdentityUser` を付ける。プロジェクトでは `roles/run.admin`・`roles/artifactregistry.writer`・`roles/iam.serviceAccountUser` が要る（GeneratorsAI の配置と同じ） |

このサービスアカウントは、Secret Manager の一覧とプロジェクト番号を読めない。そのため、issuer の URL とバケット名はシークレットから渡し、Canva の連携（`SHAPECHANGE_CANVA_*`）は既定では渡さない。4 件を Secret Manager に登録したあとは、ワークフローの配置のステップに環境変数 `DEPLOY_CANVA_SECRETS: "1"` を足す。

## 入力画像の置き場

入力画像は、バケットのライフサイクルで 7 日後に消す。この設定はバケットに 1 回入れればよく、配置のたびには要らない。2026-11-20 に、下のルールを入れた（それまでライフサイクルの設定は無かった）。

バケットは GeneratorsAI と共用で、`--lifecycle-file` はバケットの設定を丸ごと置き換える。ルールを足すときは、先にいまの設定を読み、既存のルールを残したまま足す。

```sh
gcloud storage buckets describe gs://<BUCKET> --format="json(lifecycle_config)"
```

```json
{"action": {"type": "Delete"}, "condition": {"age": 7, "matchesPrefix": ["shapechange/uploads/"]}}
```

```sh
gcloud storage buckets update gs://<BUCKET> --lifecycle-file=<ルールを足した JSON>
```

Cloud Run のサービスアカウントには、読み取り用の署名付き URL と同じ権限（オブジェクトの作成と読み取り、自分自身の `iam.serviceAccounts.signBlob`）があれば足りる。署名付き PUT URL には、形式（`Content-Type`）と大きさの上限（`x-goog-content-length-range`、10MB）を含めている。実際の GCS へのアップロードは、配置してから確かめる（2026-11-20 の時点では、偽の GCS でしか確かめていない）。

`shapechange/runs/` には消す設定を入れていない。完成画像が Drive と GCS の両方に残り続けるので、期間を決めて同じ形のルールを足すかどうかは、使ってみてから決める。

## Canva の連携

Canva 仕上げを Cloud Run でも行う場合の手順。連携の作成とトークンの取得は [src/README.md](../src/README.md) の「Canva の連携」。連携はまだ作っていないので、この手順は実際には通していない。

1. PC でトークンを取得したあと、`.env` の `SHAPECHANGE_CANVA_*` 4 件を Secret Manager へ登録する。無いシークレットは作り、有るものには新しい版を足す。値は標準入力で渡すので、画面にもコマンドラインにも出ない。

   ```sh
   python -m src.tools.refresh_canva_token --push-secrets <PROJECT_ID>
   ```

2. 上の「配置と更新」のコマンドで配置し直す。4 件がそろっていれば「Canva の連携（SHAPECHANGE_CANVA_*）を渡します。」と表示される。
3. Cloud Run のサービスアカウントに、4 件を読む権限（`roles/secretmanager.secretAccessor`）と、トークンの 2 件の版を足して消す権限（`roles/secretmanager.secretVersionManager`）があることを確かめる。版を足せないと、`shapechange_refresh_canva_token` で更新したトークンを保存できず、結果の `saved_to` が空になる。版を消せないと、更新のたびに古い版が残り、その数だけ Secret Manager の料金がかかる。

Cloud Run は、インスタンスの起動時に Secret Manager の最新版を読む。アクセストークンは短い時間で失効するので、Canva 仕上げを使う生成の前に `shapechange_refresh_canva_token` で更新する。

## コネクタの登録

1. claude.ai の「Customize」→「Connectors」で「Add custom connector」を選ぶ。
2. URL に、上の表の URL の末尾へ `/mcp` を付けたものを入れて追加する。接続の途中で合言葉の確認画面が開くので、`MCP_AUTH_PASSPHRASE` の値を入力する（配置し直したあとの再サインインでも同じ）。
3. 会話の「+」→「Connectors」で有効にする。`shapechange_` で始まるツールが 14 件見える。

2026-11-18 の配置では、登録と同じ手順（OAuth の登録・認可・トークン取得）を PC から行い、ツール一覧・形状一覧・キャラクター一覧が返ることまで確かめた。Cloud Run からの生成と Drive 保存、claude.ai への登録は未確認。

## 制約

- インスタンスは、しばらく使わないと止まり、配置のたびに入れ替わる。それ以外でも入れ替わる: 生成のジョブは、リクエストに応答を返したあとに裏で動くので、Cloud Run からは「使われていない」インスタンスに見える。2026-11-21 には、最後のリクエストから 3 分 11 秒後（ジョブが終わった 11 秒後）にインスタンスが入れ替わった。
- 入れ替わっても残すもの（GCS の `shapechange/state/` に JSON で置く。`src/mcp_server/state_store.py`）:
  - サインインの状態（登録されたクライアント、アクセストークン、リフレッシュトークン）。置くのは合言葉を鍵にしたハッシュの名前と、クライアント・権限・期限だけで、トークンそのものは置かない。アクセストークンは 24 時間、リフレッシュトークンは最後に使ってから 30 日で切れる。クライアントの登録情報（OAuth のクライアントシークレットを含むことがある）は、そのまま置く。
  - ジョブの記録（状態、引数、途中までの結果、完了時の結果）。終わったジョブは、入れ替わったあとも `shapechange_job_status` で結果を読める。
- 入れ替わると失われるもの: 動いている途中のジョブ（処理は引き継がれない。`shapechange_job_status` は `interrupted` と返す）、合言葉の確認画面を開いている途中の手続き、インスタンスの中の `outputs/`。完成画像と `run_meta.json` は、ジョブが終わった時点で Drive と GCS に残る。
- 期限の切れたトークンは、置き場から読もうとしたときに消す。読まれない記録は残り続けるので、必要なら `shapechange/state/oauth-access/` と `shapechange/state/oauth-refresh/` に限って、35 日より古いオブジェクトを消すライフサイクルを設定する。`shapechange/state/oauth-client/` は利用中でも登録時から更新されないため、年齢だけで消すルールの対象にしない。ジョブの記録は、必要な保持期間を別に決める。
- 改稿（`shapechange_iterate`）は、インスタンスが止まったあとも、GCS の `shapechange/runs/` に残した `run_meta.json` と完成画像を戻して続けられる。`shapechange_list_runs` には `location: gcs` として並ぶ。この仕組みを入れる前（2026-11-20 より前の配置）の実行は、GCS に残っていないので起点にできない。
- 実行ログの照会（`shapechange_get_run_logs`）で、インスタンスが止まったあとに返せるのは `run_meta.json` だけ。プロンプト・観察・検査ログは戻らない。完成画像は Drive、中間画像は GCS に残り、GCS の中間画像は `shapechange_list_gcs_logs` で一覧できる。
- Canva 仕上げは、ShapeChangerAI 用の連携を Secret Manager へ登録するまで行わない（連携はまだ作っていない）。
