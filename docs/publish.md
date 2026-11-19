# 公開リポジトリへの反映

開発は非公開の `100BeautiesLab_ShapeChangerAI`（`develop` → `main`）で進め、公開は `public` ブランチを通して [100BeautiesLab_ShapeChangerAI-Public](https://github.com/radiann-kswg/100BeautiesLab_ShapeChangerAI-Public) の `main` へ出す。VM はこの公開リポジトリを clone する（[deploy-vm.md](deploy-vm.md)）。決めた経緯は [requirements-5stage-connector.md](requirements-5stage-connector.md)。

`public` は、非公開の履歴とつながない別系統のブランチ。`main` と同じ内容を 1 コミットずつ積む。`main` や `develop` を `public` へ merge・rebase しない（非公開の履歴が公開側へ出てしまう）。

## 公開側に出さないもの

- 非公開リポジトリのコミット履歴と Pull Request
- Cloud Run の URL とプロジェクト番号、API キーなどの秘密の値（`.env` は元から追跡外）
- 第三者の著作物、モデル、検証用のデータ（このリポジトリには元から置かない）

`main` に入れてよいのは、そのまま公開できる内容だけにする。

## 手順

初回だけ、公開リポジトリをリモートに足す。

```sh
git remote add public https://github.com/radiann-kswg/100BeautiesLab_ShapeChangerAI-Public.git
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

1. シェーダーの公開リポジトリ [VRCShader_ShapeChange-Public](https://github.com/radiann-kswg/VRCShader_ShapeChange-Public) 側で、開発用リポジトリから配布リストのファイルを写して push する（手順は同リポジトリの README）。
2. このリポジトリの `develop` でサブモジュール `VRCShader_ShapeChange` を進め、`tests/` を実行してコミットする。寸法の読み取りが止まったら `src/shape/vrc_source.py` をシェーダーの記述に合わせる。
3. `main` へ反映してから、上の手順で公開側へ出す。
