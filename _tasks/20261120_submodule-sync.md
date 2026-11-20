# サブモジュール同期ログ — 2026-11-20 16:50

> 実機 PowerShell スクリプト `scripts/daily-submodule-sync.ps1` による自動実行。

## フェッチ・判定結果

| サブモジュール | 追跡先 | 旧 | 新 | 判定 | 備考 |
|---|---|---|---|---|---|
| `_creations-ai` | origin/master | b7ccb20 | 6f5b37d | UPDATED | FF 取り込み完了 |
| `VRCShader_ShapeChange` | origin/main | 5613487 | 5613487 | NO-CHANGE | 最新 |

## 取り込んだ更新の内容

### `_creations-ai` b7ccb20..6f5b37d

```
6f5b37d chore: sync ai-dataset (creations-db@f2d10b1f) 窶・ai_training allowed: 187 [skip ci]
4e9a69a chore: sync ai-dataset (creations-db@a4b1a43d) 窶・ai_training allowed: 187 [skip ci]
d8eb504 chore: sync ai-dataset (creations-db@fa10a498) 窶・ai_training allowed: 187 [skip ci]
f037968 fix: enforce AI opt-outs in dataset exports
fa26d38 chore: sync ai-dataset (creations-db@7849c017) 窶・ai_training allowed: 191 [skip ci]
b92e3a8 chore: sync ai-dataset (creations-db@211bff56) 窶・ai_training allowed: 191 [skip ci]
```

変更ファイル:

```
AGENTS.md                                          |   12 +
 ai-dataset/build-info.json                         |   18 +-
 ai-dataset/image-index.json                        | 7015 ++++++--------------
 ai-dataset/index.json                              |   20 +-
 ai-dataset/manifest-training.jsonl                 |  208 +-
 ai-dataset/manifest.jsonl                          | 1304 ++--
 ai-dataset/policy.json                             |   13 +-
 ai-dataset/works/Works_CommonReferences.json       |    2 +-
 ai-dataset/works/Works_DestinyFoxRecords.json      |  425 +-
 ai-dataset/works/Works_FLInvestigator78.json       |  145 +-
 ai-dataset/works/Works_NumberTales.json            | 5058 ++++----------
 ai-dataset/works/Works_PastDivers.json             |   25 +-
 ai-dataset/works/Works_ShauErRiders.json           |   12 +-
 ai-dataset/works/Works_SinisterChangingGirls.json  |   32 +-
 ai-dataset/works/Works_StardustVsKaleidoscope.json |    2 +-
 ai-dataset/works/Works_UnauthedLogica.json         |   15 +-
 ai-dataset/works/Works_UnibyteLive.json            |   85 +-
 ai-dataset/works/Works_VirtuesUs.json              |    2 +-
 creations-db                                       |    2 +-
 scripts/build-dataset.js                           |  185 +-
 scripts/lib/dataset-helpers.js                     |    5 +-
 scripts/lib/dataset-helpers.test.js                |    8 +
 scripts/lib/image-policy.test.js                   |  140 +
 scripts/lib/policy.js                              |   38 +-
 scripts/lib/policy.test.js                         |   18 +
 scripts/validate-dataset.js                        |  Bin 19493 -> 23100 bytes
 26 files changed, 4745 insertions(+), 10044 deletions(-)
```

## 最適化メモ

> 取り込んだ差分がスキーマ / `manifest-training.jsonl` / API に影響する場合は、
> Cowork の `daily-submodule-sync-optimize` タスク (Claude) に差分レビューを依頼し、
> `src/` ・ `docs/` 側の追従最適化を行うこと。本スクリプトは git 同期とログ・コミットのみ担当。

### 最適化メモ（2026-11-20、手動同期のあと）

- 差分把握: `_creations-ai` `b7ccb20..6f5b37d`。manifest に `AppearanceDetail[].Detachable`、`Formation` の `afterChanged`（扇 三春・財前 小里・神夜崎 ユノ）と `normal`／`beasted`（巳刻カミツ）、`Costume` の `onBusiness`・`private`・`jacketless` が届いた。D-Vines の 4 件は不許可になり、61 のアイドル衣装の項目と画像は無くなった（許可 191→187 件）。
- 影響判定: フィールドの削除や改名は無い。同期前のコードのままで既存のテストは通る（失敗は、以前からのスフィリアの参照画像の 1 件だけ）。
- 実施した変更: 状態と衣装の指定（`--state`・`--costume`・`--states`、コネクタの `state`・`costume`・`subjects[].state`）を足し、`Detachable` を読むようにした。要件は `docs/requirements-5stage-connector.md` の「画像入力・状態の指定・物品化」。
- 残したもの: 61 のアイドル衣装の画像の固定指定（`_AI_EXCLUDED_IMAGE_STEMS`）は、上流で消えたあとも二重の備えとして残した。
- テスト結果: `tests/` の 8 ファイルを Windows 実機で実行し、上の 1 件以外はすべて成功。

### 再同期（2026-11-20 17:40、CreationsAI #4 の修正の取り込み）

- 差分把握: `_creations-ai` `6f5b37d..64e951c`（creations-db は `f2d10b1` のまま）。`ai-dataset/image-index.json` に部位図（`AppearanceDetail[].img_PNGName`）119 件が戻った。manifest のレコード本文と許可件数（187 件）は変わらない。
- 影響判定: このリポジトリは部位図のうち `tails_unit` だけを使うので、コード変更は要らない。
- テスト結果: `tests/` の 8 ファイルを Windows 実機で実行し、以前からの 1 件（スフィリアの参照画像）以外はすべて成功。

### 再同期その 2（2026-11-20 17:50、上流の追い込みの取り込み）

- 差分把握: `_creations-ai` `64e951c..964cd5c`（creations-db `f2d10b1..f517d18`）。尻尾ユニットの画像名（`TailsUnit_PNGName`）が 7 件（2B・5・50・58・63・75・87）に入り、`images.tails_unit` に載った。ほかに部位図 3 件の宣言と、10 レコードの AIHints の構造的再同期。
- 影響判定: 追加だけで、レコードの増減と許可件数（187 件）は変わらない。コード変更は要らない。
- テスト結果: `tests/` の 8 ファイルを Windows 実機で実行し、以前からの 1 件（スフィリアの参照画像）以外はすべて成功。

### 再同期その 3（2026-11-20 17:55、『管理主』代理人の設定画の取り込み）

- 差分把握: `_creations-ai` `964cd5c..c5031d1`（creations-db `f517d18..02cfe1e`）。豹変系女子の `#DB_Regioministrators` の 3 件（扇 零夏・スフィリア、扇 三春、扇 残冬）に、別 DB の設定画を指す `Images.concept_PNGName`（`_DBCrossLinkPath`）が入り、manifest の `images.concept` に載った。途中の `dc5e6c6` では `_IsoPath` の先頭に `concept/` が重なって 3 件とも未解決だったが、`02cfe1e` で直った。
- 影響判定: 追加だけで、レコードの増減と許可件数（187 件）は変わらない。コード変更は要らない。`--joint SCG-W:4,SCG-AW:4 --dry-run` で、スフィリアに公式設定画（`cnsp_imgSCG-ENE.png`）が付き、「参照画像なしで生成します」の警告が出なくなったことを確かめた。
- テスト結果: `tests/` の 8 ファイルがすべて成功。以前から失敗していた `test_joint_run_keeps_same_slug_characters_in_separate_directories`（スフィリアの参照画像が無く観察で止まる）も通った。
