# CreationsDB / CreationsAI 更新への追従 — 2026-11-19

ユーザー依頼に基づくローカル作業。親は `develop`。既存変更なしを確認してから、CreationsAI を追跡先へ FF 更新し、CreationsDB は CreationsAI が固定する Git リンクへ合わせた。

| 対象 | 変更前 | 変更後 | 確認 |
| --- | --- | --- | --- |
| `_creations-ai` | `d4ac56f2b0c0e38ee4417a618994dd136edf0686` | `60ecce3efdb4751564c1aa5840362048a4b42424` | `origin/master` を fetch、FF |
| `_creations-ai/creations-db` | `272d5130a5f11da135a8c2ca3c66c431a04c37d5` | `51244fc580a71aa3c78361e486010cd115ec548c` | CreationsAI の固定版と `addon-ai-tag` の取得結果が一致 |

`VRCShader_ShapeChange` は今回の依頼範囲に含めず、固定版を維持した。

## 取り込んだ更新

- 外見・配色・左右基準の修正、および AIHints の構造再同期。旧・新 manifest のキャラクター行を照合し、追加・削除はなく、`data` 71件、`ai_hints` 67件、`ai_training` 11件の変更を確認した。`data` の内訳は `AIHints` 67件、`AppearanceDetail` 57件、`ColorPalette` 29件（重複あり）。
- 上流の許可件数は181→187。参考資料行を除くパイプライン対象は138キャラクター（NumberTales121、ShauErRiders6、SinisterChangingGirls11）。新しく許可された6件は ShauErRiders の `Mouse` / `Rabbit` / `Dragon` / `Snake` / `Horse` / `Cat`。
- 上流データセットの生成時刻は `2026-11-19T09:46:29+09:00`、記録する原典コミットも `51244fc5` と一致。再生成や派生ファイルの手動編集は不要と判断した。

## 最適化メモ

`src/utils/dataset.py` で形態ごとの `ai_tags` を優先し、タグがない場合だけ従来の `silhouette_notes.body_description`・`attached_items` と `outfit_features` を使う。構造再同期はタグを更新する一方、手書きの旧衣装・目色説明を残すため、両方を混ぜると新しい設定と矛盾する。実データの57で新しいスカートの記述を含み、旧ショートパンツ・旧目色説明を含まないことを確認した。

`negative_visuals` も避ける要素へ渡すが、`negative_keywords` と合わせて正の `ai_tags` と一致する項目（前後の空白・大小文字を無視）は除外する。新DBでは1番の `light pink hooded top` などが正負双方に残っていたため。`ColorPalette.Note_EN` / `Note_JP` の部位・条件注記を保持した。`AppearanceDetail.Laterality` は本人の身体基準であると要約内へ明記した。要約を共用する単体・合同プロンプト、Stage 2 の観察、Stage 4 の検査と実行記録へ同じ修正が届く。権利判定は引き続き manifest の明示的な `ai_training.allowed: true` のみを通す。

番号222・223・753などの AIHints に残る `TODO:` プレースホルダは、生成用要約へ取り込む段階の既存 `_strings()` で除外する（先頭空白・大小文字は無視）。同関数の呼出元を確認し、共通の識別・輪郭・不変特徴と形態別のタグ・旧説明・付属品・負指定のリストだけに届くことを確認した。原典や manifest は変更せず、入力レコード内の `TODO:` は保持する。

## 検証

- `tests/test_*.py` の全6ファイル、44件成功。生成・Vision・Canva は偽物を使い、課金APIを呼んでいない。
- 許可キャラクター138件すべての要約・参照画像解決を実行。新規許可6件すべてを作品とバッジで検索できた。
- 形態別の要約254件で、正のタグと避ける要素の完全一致が残らないことを確認。合成レコードで大小文字・空白を含む衝突の除外と、タグ欠落時の旧付属品説明へのフォールバックを回帰検証した。
- `--badge 57 --mode 4 --dry-run` と `--badge Mouse --work ShauErRiders --mode 4 --dry-run` が成功。57の出力で新しいタグが届き、旧説明が混ざらないことを追加で検証した。
- 追加レビュー後に全44テストを再実行し成功。1番と57番のCLI dry-runでも、必要な衣装を否定せず、新しい説明を優先することを確認した。
- TODO除外後も全44テストが成功。全254形態の要約リストにTODOがなく正負の完全一致もないこと、222・223・753の原データにTODOが残る一方CLI dry-runのプロンプトにTODOが出ないことを確認した。合成レコードでは共通・形態の項目、旧説明へのフォールバック、原文非破壊も回帰検証した。
- `node _creations-ai/scripts/validate-dataset.js` 成功（11作品、732レコード、許可187、解決済み画像参照699、未解決2）。
- `git diff --check` 成功。サブモジュール内部に未コミット変更なし。

参照画像が選べない許可キャラクターは `NumberTales-3/3`、`NumberTales-8/8`、`ShauErRiders-Dragon`、`ShauErRiders-Snake`、`ShauErRiders-Horse`。不存在画像を補完せず、既存の Stage 2 確認フローへ渡す。実画像生成・画質検査・サーバーデプロイ・commit/push は実施していない。

## 追記（同日 12:24 以降。Claude が引き継ぎ）

- `_creations-ai` は 12:24 に `bc86099`（CreationsDB は `8484bb1`）まで FF されていた。上の表と検証は `60ecce3` / `51244fc` の時点のもの。
- `bc86099` では `tests/test_dataset_gate.py` が 1 件失敗した。スフィリアに文章の設定が加わり、「文章の設定なし」を実データで確かめていた箇所が合わなくなったため。合成レコードで確かめる形に直した（この修正は `tests/` だけ）。
- 直した後、`tests/test_*.py` の全 6 ファイル 44 件が成功。許可キャラクター 138 件すべての要約を実行できた。`--badge 57 --mode 4 --dry-run` も成功。`node _creations-ai/scripts/validate-dataset.js` と形態別 254 件の再照合は、この版では行っていない。

## 追記（同日 12:31。別セッションの Claude Code による `8484bb1` の内容への追従）

12:24 の FF はこのセッションが行った（GeneratorsAI 側で受けた依頼「サブモジュールのデータ拡張を GeneratorsAI と ShapeChangerAI へ適応」による）。上の引き継ぎと同じ作業ツリーで時間が重なり、**コミット `16f47ea` には、このセッションが 12:28 に書いた `src/utils/dataset.py` の変更（下の最適化メモ）が入っている**。上の追記の「`src/` は変更なし」は、この変更を数えていない。下記のテスト 1 件・`src/README.md` の 3 段落・この追記は未コミット。

### 取り込んだ更新（CreationsDB `51244fc` → `8484bb1`）

- 獣爾騎兵 7 件（うち許可 6 件）に `AppearanceDetail` 60 項目と `ColorPalette` 50 行、豹変系女子 10 件に `AppearanceDetail` 70 項目が加わった。変わったのは 19 レコードの `AppearanceDetail` / `ColorPalette` だけで、レコードの増減と許可件数（187）の変化はない。
- 新しい書き方: 耳・尾の形状は辞書コード（`vdict_EarShapeType` / `vdict_TailShapeType`）と補足（`about_*`）、尾や翼の本数は `value_Num`、豹変前後は `Formation: beforeChange / afterChanged`、通常制服などの適用範囲は項目の `Note_*`。

### 最適化メモ

- 従来の要約は `value_EN` / `value_JP` と辞書コードだけを読み、補足・本数・項目の注記と、`Formation` が形態名でない項目（豹変前後の 15 項目。ミルの通常時の髪と尾を含む）を落としていた。`_attr_text()` と `_appearance_lines()` で要約へ入れた。NumberTales の耳の補足（1 番の `left ear droops` など）も同じ修正で届く。
- `beforeChange` は通常の項目として扱う。それ以外の状態は「別状態だけの特徴。今回の絵には描かない」と付けて残す。公式設定画は両方の状態を描いているため、捨てると Stage 2 の観察と Stage 4 の検査が通常の姿の特徴と取り違える。既定の状態は `_DEFAULT_STATES` の 1 語だけで、状態を選ぶオプションは作っていない。
- `tail_count` は `TailsUnit` が無いとき、尾の項目の `#DesignAttr_Count` を使う（獣爾騎兵の許可 6 件が 1 本。翼の本数と別状態の尾は数えない）。Stage 4 の本数の照合が獣爾騎兵にも効く。
- 追従しなかったもの:
  - 獣爾騎兵の制服共通色（作品メタの `$EnumDef_CommonColor` 7 色）。manifest に無く、どのキャラクターが使うかの記録も個別パレットから除かれた（巳は黄を使わない、子と寅は別配色）。一律に足すと使っていない色を指定する。衣装の色は `AppearanceDetail` の文と `#ColorRole_CommonOverride` の行に残っている。
  - 豹変系女子 N の、NumberTales 0 からの外見の継承。manifest に展開されないため、N は配色と参照画像だけになる。
  - どちらも CreationsAI の manifest が持つようになれば読める。

### 検証

- `tests/test_*.py` 全 6 ファイル 45 件成功（追加 1 件: 辞書コード・補足・本数・注記・状態の要約と、実データのハッカ・ルノ・57 の確認）。
- 許可キャラクター 138 件（NumberTales 121、獣爾騎兵 6、豹変系女子 11）の全形態を要約。別状態の行は豹変系女子の 10 行、`AppearanceDetail` 由来の本数は獣爾騎兵 6 件、辞書コードや `TODO:` がそのまま出る行は 0。
- `--badge Mouse --work ShauErRiders --mode 4 --dry-run`、`--badge E --work SinisterChangingGirls --mode 4 --dry-run`、`--badge 57 --mode 4 --dry-run` が成功し、プロンプトに上の内容が出た。
- 生成・Vision・Canva の API は呼んでいない。commit / push はしていない。

## 追記（同日 12:45。上の未コミット分のコミット）

- 12:31 の追記にある未コミット分（テスト 1 件、`src/README.md` の 3 段落、追記）は、公開用ブランチを作る前に内容をそろえるため、引き継ぎ側の Claude が 1 コミットにまとめた。コミット前に `tests/test_*.py` の全 6 ファイル 45 件が成功することを確かめた。

## 追記（同日 13:05。バッジでのキャラクター指定）

ユーザーの依頼「GeneratorsAI も ShapeChangerAI も Badge でキャラクターを指定できるようにする」による変更。GeneratorsAI 側のセッションが行い、`develop` に 1 コミットで入れた（push はしていない）。

- `--badge` が CreationsDB のインデックスバッジを受け付ける。獣爾騎兵は `EZ1`（従来は獣名の `Mouse` だけ）、NumberTales は `57` / `2B`、豹変系女子は `ENE`。作品コード付き（`NTS-57` / `SAR-EZ1`）なら `--work` は要らない。番号と作品内の識別子（`2-alt` / `Mouse`）は従来どおり使える。
- バッジは作品別 `db_type.json` の `$IndexDef.$badge` の宣言から組み立てる（`src/utils/dataset.py` の `record_badge()`。CreationsDB の `lib/graph/graph-badge.js` と同じ規則）。獣爾騎兵は辞書 `dict_Beast.json` の `Beast_Badge` を引く。番号や獣名から機械的には作らない。
- `--list-characters` とコネクタの `shapechange_list_characters` に、作品コード付きのバッジを足した（CLI は 2 列目、コネクタは `badge`）。実行フォルダー名などに使う識別子（`ShauErRiders-Mouse`）は変えていない。
- 検証: `tests/test_*.py` 全 6 ファイル 46 件成功（追加 1 件）。許可キャラクター 138 件すべてが、作品コード付きのバッジ 1 語で本人に届く。`--badge SAR-EZ1 --mode 4 --dry-run` と `--joint SAR-EZ1:0,SAR-EZ4:3 --dry-run` が成功。生成の API は呼んでいない。

## 自動同期の追補 — 2026-11-19（スケジュール実行・サブモジュール追従ボット）

| サブモジュール | 追跡先 | 旧 | 新 | 判定 |
|---|---|---|---|---|
| `_creations-ai` | origin/master | bc86099 | b7ccb20 | UPDATED |
| `_creations-ai/creations-db`（ネスト） | CreationsAI の gitlink | 8484bb1 | 688a349 | 親の記録に追従 |
| `VRCShader_ShapeChange` | origin/main | 5613487 | 5613487 | NO-CHANGE |

取り込み範囲:

```text
b7ccb20 chore: sync ai-dataset (creations-db@688a3492) — ai_training allowed: 187 -> 191 [skip ci]

 18 files changed, 112 insertions(+), 85 deletions(-)

creations-db 8484bb1..688a349:
688a3492 AIHints調整(対象DB拡張周り) 続き
563a4763 AIHints調整(対象DB拡張周り)
3e3e52f0 Merge branch 'develop' into addon-ai-tag
1c70567d DB情報追加(獣爾騎兵＆豹変系女子) `addon-ai-tag`向け
09b69694 AIHints拡張(獣爾騎兵＆豹変系女子) 続き
44805400 AIHints拡張(獣爾騎兵＆豹変系女子)
 23 files changed, 3692 insertions(+), 63 deletions(-)
```

### 最適化メモ

- 差分把握: CreationsAI は dataset の再生成のみ（manifest のフィールド追加・削除なし）。豹変系女子 `#DB_Regioministrators` の AI 利用が許可され（許可 187→191 件）、獣爾騎兵・豹変系女子の AIHints が拡張された（AIHints 付き 122→139 件）。
- 影響判定: スキーマ・フィールド名・API・参照パスの変更はない。ただし既存テスト 3 件が失敗し、データ変化による実害を 2 点確認した。
  1. 新しく許可された Regioministrators の 4 件は作品内の識別子が `W`/`X`/`Y`/`Z`（バッジは `AW`〜`AZ`）で、`W` が `#DB_Primary` の神夜崎 ユノ（方位 `W`）と重なる。扇 零夏・スフィリアは Primary`ENE` と Regioministrators`AW` の 2 レコードになった。
  2. 名前「スフィリア」が同じ作品内で 2 件に該当し、名前検索が複数該当エラーになる（仕様どおりの挙動）。
- 実施した変更:
  - `src/utils/dataset.py`: `find_character(badge=...)` は Num・Num_Badge・インデックスバッジで先に照合し、該当が無いときだけ作品内の識別子（`Mouse` / `W`）で探す。`SCG-W` はユノ、`SCG-AW` は Regioministrators のスフィリアに届く。
  - `tests/test_dataset_gate.py`: スフィリアの指定を `SCG-ENE` に変え、名前だけの指定が絞れないこと、`SCG-W`/`SCG-AW` の解決を追加。
- 未対応（PR 時のレビュー所見）: `character_slug()` は識別子を使うため、ユノと Regioministrators のスフィリアがどちらも `SinisterChangingGirls-W` になる。実行フォルダーは run-id で分かれるが、記録上の識別は紛らわしい。
- テスト結果: 課金キーを空にして `python -m pytest tests -q` → 46 件成功。
