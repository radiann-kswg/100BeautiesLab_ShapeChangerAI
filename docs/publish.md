# 公開リポジトリでの開発

開発も公開も [100BeautiesLab_ShapeChangerAI](https://github.com/radiann-kswg/100BeautiesLab_ShapeChangerAI) で行う（`develop` → Pull Request → `main`）。VM はこのリポジトリの `main` を clone する（[deploy-vm.md](deploy-vm.md)）。ブランチの役割は [AGENTS.md](../AGENTS.md#git-ブランチ運用) にある。

2026-11-19 の途中までは、非公開の `100BeautiesLab_ShapeChangerAI-Develop` で開発し、履歴をつながない `public` ブランチを通して、このリポジトリの `main` へ 1 コミットずつ写していた。同じ日に開発をこのリポジトリへ移し、`-Develop` は使わなくなった（削除する予定）。非公開側のコミット履歴と Pull Request は持ってきていない。移した時点の内容は、`develop` の 1 コミット（`754864d`）に入っている。公開を決めた経緯は [requirements-5stage-connector.md](requirements-5stage-connector.md)。

## コミットに入れないもの

`develop` も作業ブランチも、push した時点で公開される。

- Cloud Run の URL とプロジェクト番号、API キーなどの秘密の値（`.env` は元から追跡外）
- 第三者の著作物、モデル、検証用のデータ（このリポジトリには元から置かない）

push する前に、Cloud Run の URL が入っていないことを確かめる。

```sh
git grep -nE "[0-9]{10,}\.[a-z0-9-]+\.run\.app" develop    # 何も出ないこと
```

## リモートの向き

`origin` をこのリポジトリに向ける。

```sh
git remote set-url origin https://github.com/radiann-kswg/100BeautiesLab_ShapeChangerAI.git
git remote -v
```

2026-11-19 より前からある作業コピーには、`-Develop` を向いたリモートと、非公開の履歴を持つブランチ（当時の `main`・`develop`・`public` や作業ブランチ）が残っていることがある。それらをこのリポジトリへ push すると、非公開の履歴が公開される。`git fetch origin` のあと、`origin/develop` から作り直したブランチを使う。

## シェーダーを更新したとき

1. シェーダーの公開リポジトリ [VRCShader_ShapeChange](https://github.com/radiann-kswg/VRCShader_ShapeChange) 側で、開発用リポジトリ（`VRCShader_ShapeChange-Develop`）から配布リストのファイルを写して push する（手順は同リポジトリの README）。
2. このリポジトリの `develop` でサブモジュール `VRCShader_ShapeChange` を進め、`tests/` を実行してコミットする。寸法の読み取りが止まったら `src/shape/vrc_source.py` をシェーダーの記述に合わせる。
3. Pull Request で `main` へ反映する。VM を更新する場合は、[deploy-vm.md](deploy-vm.md) の「セットアップと更新」を実行する。
