# VM への配置（使うときだけ起動する SSH/CLI 実行基盤）

GeneratorsAI の VM と同じ方針で、GCE の VM `generators-ai`（プロジェクト `claude-radiannkswg`、ゾーン `us-central1-f`、Debian 13）に相乗りする。VM は使うときだけ起動し、MCP クライアントからは SSH 越しの stdio でつなぐ。公開ポート・Caddy・DNS は使わない。決めた経緯は [requirements-5stage-connector.md](requirements-5stage-connector.md)。

完成画像の Drive への保存は MCP サーバー（`shapechange_*` ツール）が行う。VM 上で CLI を直接実行した場合、出力は VM の `outputs/` に残り、Drive には上がらない。

## VM に置くもの

| 場所 | 内容 |
| --- | --- |
| `/opt/shapechanger` | 公開リポジトリ [100BeautiesLab_ShapeChangerAI](https://github.com/radiann-kswg/100BeautiesLab_ShapeChangerAI) の clone（`main`）、`.venv`、`.env`、`outputs/` |
| `/opt/shapechanger/_creations-ai` | キャラクターデータのサブモジュール |
| `/opt/shapechanger/VRCShader_ShapeChange` | 公開用のシェーダーパッケージのサブモジュール。パイプラインはここの 2 ファイルから寸法を読む |

VM が取得するのは公開リポジトリだけ。シェーダーの非公開の開発用リポジトリ（Unity プロジェクトの `VRCShader_ShapeChange-Develop`）と、モデルや検証用のデータは VM に置かない。鍵の登録も要らない。

2026-11-19 より前は、開発用の非公開リポジトリ（のちの `100BeautiesLab_ShapeChangerAI-Develop`。開発は同じ日にこのリポジトリへ移した）を読み取り専用のデプロイキーで clone し、シェーダーの 2 ファイルを PC から `/opt/shapechanger-shape` へ送っていた。[deploy/setup_vm.sh](../deploy/setup_vm.sh) を実行すると公開側へ切り替わる。残った `/opt/shapechanger-shape` と `~/.ssh/shapechanger_deploy` はもう使わないので、消して構わない。`100BeautiesLab_ShapeChangerAI-Develop` に登録したデプロイキー（`generators-ai VM (read-only)`）も不要になる。

2026-11-19 に、この手順で VM を公開側へ切り替えた。自己チェックと 14(トヨ) の `--dry-run` が通り、Drive はトークンの更新まで確かめた。切り替え後の生成と Drive への保存も、同じ日に VM の上で確かめた（14(トヨ) の膨体を 5 段階で生成し、完成画像が保存先フォルダーに入った）。

## 起動と停止

```sh
gcloud compute instances start generators-ai --zone=us-central1-f
gcloud compute instances stop  generators-ai --zone=us-central1-f
```

固定 IP は予約していないので、外部 IP は起動のたびに変わる。使い終わったら停止する。長く使わない場合は、`/opt/shapechanger/.env` を消してから停止する（次回 `setup_vm.sh` を実行すれば作り直される）。

## セットアップと更新

PC のリポジトリのルートで実行する。`setup_vm.sh` は何度実行しても壊れないので、初回もコードの更新も同じ手順になる。

```sh
gcloud compute scp deploy/setup_vm.sh generators-ai:setup_shapechanger_vm.sh --zone=us-central1-f
gcloud compute ssh generators-ai --zone=us-central1-f --command "bash ~/setup_shapechanger_vm.sh"
```

スクリプトが行うこと:

1. git と Python の venv を用意する。
2. `/opt/shapechanger` へ公開リポジトリを clone する。2 回目以降は取得し直して `main` の内容へ合わせる（公開側は履歴が別系統なので pull は使わない。VM で追跡ファイルを直接書き換えていた場合、その変更は捨てられる）。続けて 2 つのサブモジュールを取得する。
3. `requirements-mcp.txt` を venv に入れる。
4. `.env` を作り直す。`GEMINI_API_KEY`・`OPENAI_API_KEY`・`DRIVE_*` は Secret Manager の最新版を VM のサービスアカウントで読む（PC を経由しない）。`OUTPUT_SINK=drive`・`MCP_TRANSPORT=stdio` を書き、モデル名などの秘密でない行（`GEMINI_REFERENCE_MODEL`・`GPT_MODEL`・`OPENAI_IMAGE_QUALITY`・`GCS_BUCKET` など）は既存の `.env` から引き継ぐ。
5. 生成 API を呼ばない範囲で確かめる（依存の import、`--list-modes`、`--dry-run`）。

VM が使うのは公開リポジトリの内容なので、コードやシェーダーの更新は、先に [publish.md](publish.md) の手順で公開側へ出してから、上のコマンドを実行する。

Canva の値は入れない。ShapeChangerAI 用の連携ができるまで、VM では Canva 仕上げを省いた結果になる。

モデルを PC の `.env` と同じにする場合は、VM の `.env` に行を足す（一度足せば、作り直しても引き継がれる）。

```sh
gcloud compute ssh generators-ai --zone=us-central1-f --command "echo GEMINI_REFERENCE_MODEL=models/gemini-3.1-flash-image >> /opt/shapechanger/.env"
```

## MCP クライアントからつなぐ

SSH 越しに、VM の上で MCP サーバーを stdio で起動する。サーバーは `.env` を自分で読む。

```sh
claude mcp add --transport stdio --env CLOUDSDK_SSH_PUTTY_FORCE_CONNECT=false shapechange-vm -- gcloud compute ssh generators-ai --zone=us-central1-f --project=claude-radiannkswg --quiet --ssh-flag=-T --command "cd /opt/shapechanger && exec .venv/bin/python -m src.mcp_server"
```

- `shapechange_*` のツールが 10 件見えればつながっている。
- **Windows では `CLOUDSDK_SSH_PUTTY_FORCE_CONNECT=false` が要る。** 無いと gcloud が plink の標準入力へ `y` を流し込み、クライアントからの通信がサーバーへ届かない。
- **VM を起動した直後は、先に 1 回ふつうに SSH する**（`gcloud compute ssh generators-ai --zone=us-central1-f --command true`）。外部 IP が変わると plink がホスト鍵の確認を求め、上の設定ではそこで止まるため。
- ジョブはサーバーのメモリー上にある。クライアントを閉じて SSH が切れると、実行中のジョブも止まる。実行の記録は VM の `outputs/` に残る。
- 2026-11-18 に確かめたのは、PC から上のコマンドと同じ経路（`gcloud.cmd` を子プロセスで起動）でのツール一覧の取得まで。`claude mcp add` での登録そのものは未確認。

## VM で実行しないもの

`tests/` は VM で実行しない。開発用の作業フォルダーが前提で、VM の `.env` は `OUTPUT_SINK=drive` になっている。VM での確認は `setup_vm.sh` の自己チェックと `--dry-run` で行う。
