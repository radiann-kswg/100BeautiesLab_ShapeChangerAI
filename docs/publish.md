# 公開リポジトリへの反映

開発は非公開の `100BeautiesLab_ShapeChangerAI-Develop`（`develop` → `main`）で進め、公開は `public` ブランチを通して [100BeautiesLab_ShapeChangerAI](https://github.com/radiann-kswg/100BeautiesLab_ShapeChangerAI) の `main` へ出す。VM はこの公開リポジトリを clone する（[deploy-vm.md](deploy-vm.md)）。決めた経緯は [requirements-5stage-connector.md](requirements-5stage-connector.md)。

`public` は、非公開の履歴とつながない別系統のブランチ。`main` と同じ内容を 1 コミットずつ積む。`main` や `develop` を `public` へ merge・rebase しない（非公開の履歴が公開側へ出てしまう）。

## 公開側に出さないもの

- 非公開リポジトリのコミット履歴と Pull Request
- Cloud Run の URL とプロジェクト番号、API キーなどの秘密の値（`.env` は元から追跡外）
- 第三者の著作物、モデル、検証用のデータ（このリポジトリには元から置かない）

`main` に入れてよいのは、そのまま公開できる内容だけにする。

## リモートの向き

2026-11-19 に名前を入れ替えた。元の名前 `100BeautiesLab_ShapeChangerAI` は公開リポジトリが引き継ぎ、開発用は `100BeautiesLab_ShapeChangerAI-Develop` になった（シェーダーも同じで、公開が `VRCShader_ShapeChange`、開発用が `VRCShader_ShapeChange-Develop`）。作業コピーでは `origin` を開発用、`public` を公開リポジトリに向ける。

```sh
git remote set-url origin https://github.com/radiann-kswg/100BeautiesLab_ShapeChangerAI-Develop.git
git remote -v
```

入れ替えより前に clone した作業コピーは、`origin` が元の名前のままなので、公開リポジトリを指している。そのまま `develop` や作業ブランチを push すると、非公開の履歴が公開側へ出る。上のコマンドで直してから使う。

## 手順

初回だけ、公開リポジトリをリモートに足す。

```sh
git remote add public https://github.com/radiann-kswg/100BeautiesLab_ShapeChangerAI.git
```

`main` の内容を `public` へ 1 コミットとして写し、両方へ push する（PowerShell でもそのまま動く）。

```sh
git fetch origin
git grep -nE "[0-9]{10,}\.[a-z0-9-]+\.run\.app" origin/main    # 何も出ないこと（Cloud Run の URL）
git commit-tree "origin/main^{tree}" -p public -m "Sync from main"   # 表示されたコミット ID を次の行で使う
git update-ref refs/heads/public <コミット ID>
git push origin public
git push public public:main
```

そのあと VM を更新する場合は、[deploy-vm.md](deploy-vm.md) の「セットアップと更新」を実行する。

## シェーダーを更新したとき

1. シェーダーの公開リポジトリ [VRCShader_ShapeChange](https://github.com/radiann-kswg/VRCShader_ShapeChange) 側で、開発用リポジトリ（`VRCShader_ShapeChange-Develop`）から配布リストのファイルを写して push する（手順は同リポジトリの README）。
2. このリポジトリの `develop` でサブモジュール `VRCShader_ShapeChange` を進め、`tests/` を実行してコミットする。寸法の読み取りが止まったら `src/shape/vrc_source.py` をシェーダーの記述に合わせる。
3. `main` へ反映してから、上の手順で公開側へ出す。
