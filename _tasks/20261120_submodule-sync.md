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
