# 5 段階パイプラインとコネクタの要件（2026-11-18 合意）

ラジアンとの要件定義で決めたことと、保留にしたことの記録。実装の使い方は [src/README.md](../src/README.md)。

## 決めたこと

**パイプライン**

| 項目 | 決定 |
| --- | --- |
| Stage 1 | キャラクターデータと形状仕様から決定的にプロンプトを組む。LLM の推敲とランダムシーンは入れない |
| Stage 2 | 公式設定画を Vision で観察する。失敗時は続行／中止の回答を待ち、無回答は中止 |
| Stage 3 | Gemini でラフを生成。既定は単体 3 枚、合同は 1 体あたり 2 枚。Adobe の構図ガイドと SDXL は持ち込まない |
| Stage 4 | OpenAI の `GPT_MODEL` で検査。形状と違反の指摘が 3 件以上なら作り直し、それ未満はラフを起点に修正 |
| Stage 5 | 検査を通ったラフから完成画像を清書（既定 1 枚）。Canva 仕上げは連携があれば行い、省略できる |
| 単発生成 | `--quick` として残す。`--dry-run` は共通 |
| 自然文 | 複数に読める形状語は代表の形状に決め打ちし、選んだ形状を返答に明記する |
| 合同生成 | 2〜4 体。キャラクターごとに別の形状を指定できる |
| 改稿 | 以前の完成画像を起点に Stage 3〜5 をやり直す |

**コネクタ**（MCP サーバー `shapechange_mcp`）

- ShapeChangerAI 専用のサーバーにし、GeneratorsAI のコネクタとは分ける。
- ツール: 単体生成、合同生成、自然文からの依頼、改稿、ジョブ状況、確認待ちへの回答、実行一覧、実行ログ、形状一覧、キャラクター一覧。
- Drive へは完成画像だけを、GeneratorsAI と同じフォルダーに `shapechange_` で始まる名前で置く。中間画像は `GCS_BUCKET` があれば GCS、無ければサーバー内。
- HTTP で公開する場合の認証は、GeneratorsAI と同じ自動承認の OAuth。本人確認はしない。
- Canva は ShapeChangerAI 用の連携を別に作る（リフレッシュトークンを GeneratorsAI と取り合わないため）。
- 名前: VM での配置は `/opt/shapechanger`、Cloud Run のサービスは `shapechanger-ai`。

**VM での動かし方**（2026-11-18 決定。手順は [deploy-vm.md](deploy-vm.md)）

- GeneratorsAI の VM と同じ方針にする。既存の VM `generators-ai` に相乗りし、使うときだけ起動して、MCP クライアントから SSH 越しの stdio でつなぐ。公開ポート・Caddy・DNS・固定 IP は使わない。
- 当初の案（同じ VM に systemd と Caddy で常駐させ、サブドメインで公開する）は見送った。決めた時点で、GeneratorsAI の公開コネクタは Cloud Run へ移っており、VM は停止中で固定 IP も無かった。当初の案のために用意した systemd ユニットと Caddy の設定は、一度も動かさないまま 2026-11-18 に削除した。
- VM の操作は、ラジアンの PC の gcloud でエージェントが行う。
- コードは、VM に置いた読み取り専用のデプロイキーで GitHub から clone する。VM が使うブランチは `main`（PR #1 のマージ後に `develop` から切り替えた）。 2026-11-19 に、公開リポジトリを HTTPS で clone する形へ変えた（下の「公開リポジトリへの移管」）。
- `.env` の秘密の値は Secret Manager から VM が直接読む。PC の `.env` にある `DRIVE_*` は削除済みの OAuth クライアントの値で、使えない。

**公開コネクタ**（2026-11-18 決定。手順は [deploy-cloud-run.md](deploy-cloud-run.md)）

- claude.ai のコネクタとして使う公開の配置は、GeneratorsAI のコネクタと同じく Cloud Run にする。サービスは `shapechanger-ai`（`asia-northeast1`）で、インスタンス数・メモリー・タイムアウト・秘密の値の渡し方は `generators-ai` と同じ。
- 公開範囲も GeneratorsAI と同じにする（認証なしで呼べる Cloud Run と、自動承認の OAuth）。URL を知っていれば誰でも課金を伴う生成を呼べることは承知のうえで、URL を公開の場に書かない運用にする。
- 配置は PC からスクリプト（`deploy/deploy_cloud_run.py`）で行う。送るのは、コミット済みのコード、キャラクターデータ、シェーダーの 2 ファイルだけ。

**進め方**

- 2026-11-18 に作業ブランチ `shape-pipeline` を `main` へマージした。以後は [Git ブランチ運用](../AGENTS.md#git-ブランチ運用) のとおり `develop` で進め、`main` へは Pull Request で反映する。
- エージェントは区切りごとにコミットする。push と Pull Request の作成は、ラジアンが依頼したときに行う。

## 公開リポジトリへの移管（2026-11-19 決定）

配布や AI への入力ができないデータ（検証用のアバター、公開作例の調査資料など）を公開の場に出さないまま、コードとシェーダーを公開するための構成。手順は [publish.md](publish.md)。

| 項目 | 決定 |
| --- | --- |
| 公開の形 | GitHub は非公開リポジトリの一部のブランチだけを公開できないので、新しい公開リポジトリを作る。今の非公開リポジトリは開発用に残す |
| 公開リポジトリ | このリポジトリは `100BeautiesLab_ShapeChangerAI-Public`、シェーダーは `VRCShader_ShapeChange-Public`。どちらも、履歴を持たない `public` ブランチを公開側の `main` へ push する |
| 公開までの順序 | 公開リポジトリは非公開で作り、ラジアンが中身を確かめてから公開へ切り替える |
| ライセンス | このリポジトリは GeneratorsAI と同じ CC BY-NC 4.0（[LICENCE.md](../LICENCE.md)）。シェーダーは MIT License |
| シェーダーの公開範囲 | 開発側の配布リスト（`Tools > ShapeChange > Export Shader Only`）にあるファイルだけ。VCC / ALCOM から入れられる VPM パッケージ（`net.numbertales-radiann.shapechange`）にする |
| サブモジュール | `VRCShader_ShapeChange` の取得元を公開のシェーダーリポジトリへ変える。寸法を読む 2 ファイルはパッケージのルートにある |
| VM | 公開リポジトリを HTTPS で clone し、サブモジュールを 2 つとも取得する。デプロイキーと、PC からシェーダーの 2 ファイルを送る手順はやめる |
| 公開の場に書かない値 | Cloud Run の URL とプロジェクト番号は、このリポジトリの文書から外した（非公開側の過去のコミットには残る） |
| Drive の確認 | VM を切り替えたあと、VM で 1 枚生成して Drive への保存まで通す |

## 保留にしたこと

形状変化シェーダーの最終調整は 2026-11-18 に済み、サブモジュールを検証用アセット退避後の版（`29ba0bf`）へ更新した。VM まわりはここから進める。

- [x] VM への配置（2026-11-18）。`/opt/shapechanger` に clone し、形状データはシェーダーの 2 ファイル（`ShapeChange.hlsl`、`ShapeChangeProperties.lilblock`）だけを `/opt/shapechanger-shape` に置いた。`VRCShader_ShapeChange` のフォルダーやサブモジュール、検証用のデータは VM に置かない。
- [x] デプロイの担当、コードの届け方、VM 用のセットアップスクリプトと手順書（2026-11-18）。上の「VM での動かし方」と [deploy-vm.md](deploy-vm.md)、[deploy/setup_vm.sh](../deploy/setup_vm.sh)。
- [x] HTTP での公開（2026-11-18）。Cloud Run の `shapechanger-ai` に配置した。OAuth の登録・認可・トークン取得を経て、ツール一覧（10 件）、形状一覧（16 件）、キャラクター一覧を取得できることを確かめた。
- [ ] Cloud Run からの生成と Drive 保存の確認。課金を伴うため、配置の時点では行っていない。同じ資格情報での Drive 保存は VM で確認済み。
- [x] claude.ai へのコネクタの登録（2026-11-18、ラジアンが登録）。
- [ ] GitHub Actions からの自動デプロイ。GeneratorsAI には有るが、こちらは未整備。シェーダーの 2 ファイルは、2026-11-19 から公開のサブモジュールで取得できるようになった（ビルドへ渡す方法の課題は解消）。
- [ ] 中間画像の置き場。Cloud Run は GCS（`generators-ai-outputs` の `shapechange/`）へ置く設定にしたが、生成を伴うため未確認。VM の `.env` には `GCS_BUCKET` を入れていないので、VM では Stage 3/4 の画像が `outputs/` に残るだけで、クライアントからは見えない。
- [ ] ShapeChangerAI 用の Canva 連携の作成と、アクセストークンを更新する仕組み。今あるのは書き出し処理だけで、偽の API でしか確かめていない。
- [x] シェーダー調整後の `src/shape/mode_descriptions.json` の見直し（2026-11-18）。寸法の読み取り結果は調整前と同じ。言葉は、膨体とコインの「形を保つ耳・尾」、上面顔円柱の顔、板・反物の顔（頭も同じ幅まで横に広がる）を直した。この記述での本番生成はまだ行っていない。
- [x] Drive への実アップロードの確認（2026-11-18）。PC から SSH 越しに VM のコネクタへつなぎ、`shapechange_generate_character` で 42 の真球を 5 段階で生成した。完成画像 1 枚が、保存先フォルダーに `shapechange_20261118_062525_gemini_mode04_NumberTales-42__stage5_final_mode04_NumberTales-42_01.jpg` として入った。PC の `.env` の `DRIVE_*` は `deleted_client`（OAuth クライアントが削除済み）のままなので、PC から Drive へ上げる場合は Secret Manager の値に入れ替える必要がある。

## 初回の本番実行で分かったこと（2026-11-18、85 の箱化）

- 5 段階は最後まで動いた。検査がラフ 1 枚の尾の本数を拾い、修正で先端が 8 本前後に増えた。
- 検査モデルに設定の本数を見せると、尾が 5〜6 本の絵にも「8 本」と答えた。尾の本数と体数は、設定を知らせない別の呼び出しで数えさせ、照合をコードで行うように変えた。数え方には 1〜2 本の誤差がある。
- 変更後の方式での 5 段階の本番実行は、2026-11-18 に VM で 1 回行った（42 の真球、腕と脚は残す指定）。ラフ 3 枚の尾の先端は 9 本・5 本・2 本と数えられ（設定は 4 本）、3 枚とも修正に回った。清書した完成画像は検査していない。脚は「元の太さのまま外へ出す」指定だったが、完成画像では足先だけが球の下から出ている。
- 合同生成・改稿・自然文の本番実行は、まだ行っていない。

## VM への配置で分かったこと（2026-11-18）

- VM で `tests/` を実行したところ、テスト用の 40 バイトの画像が 2 枚、実際の Drive フォルダーへ上がった。テストは `OUTPUT_SINK` を環境変数から消していたが、パイプラインが `.env` を読み直して `OUTPUT_SINK=drive` に戻していた。2 枚は Drive のゴミ箱へ移し、テストは `OUTPUT_SINK=local` を明示するように直した。VM では `tests/` を実行しない。
- Windows の `gcloud compute ssh` は、既定で plink の標準入力へ `y` を流し込む。SSH 越しの stdio では `CLOUDSDK_SSH_PUTTY_FORCE_CONNECT=false` を付けないと通信が届かない。GeneratorsAI の手順書（`docs/usage-mcp-multi-llm-gce.md` §7.5）のコマンドも、この PC では同じ設定が要るはず（未確認）。
- VM では形状データが Git の管理外なので、`run_meta.json` の入力元のコミットは空になる。シェーダーの版は 2 ファイルの SHA-256 で記録される。

## Cloud Run への配置で分かったこと（2026-11-18）

- キャラクターデータには日本語の名前のファイルが 3 件ある。Windows の `gcloud run deploy --source` にフォルダーのまま送らせると、ビルドは通るが、Cloud Run がイメージを取り込めず失敗した（`Container import failed`）。データを Python で tar にまとめて送り、Dockerfile の `ADD` で展開する形にして解消した。
- Cloud Run の URL は「サービス名-プロジェクト番号.リージョン.run.app」の形でも決まるので、初回のデプロイから OAuth の issuer を渡せる。
