# src — 形状変化イラスト生成パイプライン

創作キャラクターのデータと形状変化シェーダーの寸法からプロンプトを組み立て、ラフの生成・検査・修正・清書までを行う。作業ルールは [AGENTS.md](../AGENTS.md) が正本。合意した要件と保留事項は [docs/requirements-5stage-connector.md](../docs/requirements-5stage-connector.md)。

## 入力

| 入力 | 読む場所 | 使う内容 |
| --- | --- | --- |
| キャラクター | `_creations-ai/ai-dataset/manifest.jsonl` | 名前・身長・外見の特徴・尾の構造・配色・AI 利用可否・公式画像のパス |
| 参照画像 | `_creations-ai/creations-db/data/` | 対象キャラクター本人の公式イラスト・設定画・尾の部位図・デザイン表（最大 4 枚）。ほかのキャラクターも一緒に写るイラスト（2 件以上のレコードが参照する絵）は、本人だけの画像があれば使わない |
| 形状の寸法 | `VRCShader_ShapeChange/`（公開用のシェーダーパッケージ）の `ShapeChange.hlsl` と `ShapeChangeProperties.lilblock` | 16 形状の完成寸法（変形前の身長を 1 とした比）、厚み、顔の比率、四肢と付属物の既定 |
| 形状の記述 | [`shape/mode_descriptions.json`](shape/mode_descriptions.json) | 輪郭・顔の位置・面と衣装の割り当て・途中の姿（2D 作画向けの言葉） |

寸法はこのリポジトリへ写さず、実行のたびにシェーダーから読む。シェーダーの記述が変わって読めなくなった場合は、古い値で続けずエラーで止まる。読むのはこの 2 ファイルだけ。

外見の要約は、CreationsDB の構造再同期で更新される `ai_hints.forms.<形態>.ai_tags` を優先する。タグがない場合だけ `silhouette_notes.body_description`・`attached_items` と `outfit_features` を使う。手書きの旧説明を新しいタグへ混ぜない。`negative_visuals` は `negative_keywords` とともに生成時に避ける要素へ渡すが、正のタグと同じ項目（前後の空白・大小文字は無視）は除外する。`AppearanceDetail.Laterality` の左右はキャラクター本人の身体が基準で、配色の `Note_EN` / `Note_JP`（色を使う部位や条件の注記）もプロンプト・観察・検査に残す。

共通・形態別の AIHints から要約へ取り込むリストでは、`TODO:` で始まる未記入項目（先頭の空白・大小文字は無視）を除外する。原典や manifest の内容は変更しない。

`AppearanceDetail` の属性は、辞書コード（`vdict_EarShapeType` など）を末尾の語で、個体差の補足（`about_EN` / `about_JP`）を括弧で、本数（`value_Num`）を `count: 1` の形で要約へ入れる。項目の `Note_EN` / `Note_JP`（通常制服などの適用範囲）も残す。`TailsUnit` を持たない作品では、尾の項目に書かれた本数を Stage 4 の本数の照合に使う。

`Formation` が `humanoid` / `corefolder` 以外の項目は、作品内の状態（豹変系女子の `beforeChange` / `afterChanged`、獣爾騎兵の `normal` / `beasted`）として扱う。`beforeChange` と `normal` は通常の項目として描く。それ以外の状態の項目は「別状態だけの特徴。今回の絵には描かない」と付けて要約に残す。公式設定画には両方の状態が描かれているため、項目を捨てると観察と検査が通常の姿の特徴と取り違える。`--state` に状態の名前を渡すと、その状態の項目を通常の項目として描き、既定の状態の項目に「別状態だけの特徴」を付ける（下の「状態と衣装の指定」）。

`AppearanceDetail` の `Costume`（衣装）が `usual` 以外の項目は、manifest を読む時点で落とす。AIHints に写された同じ項目の文面も落とす。描くのは通常衣装だけで、別衣装（NumberTales 35 の `miko`、60 の `otaku` など）の特徴を混ぜない。`Costume` を持たない項目は、どの衣装でも描く特徴として残す。`--costume` に衣装の名前を渡すと、その衣装の項目を残し、通常衣装の項目と文面を落とす。

権利上 AI に渡さない画像（`_AI_EXCLUDED_IMAGE_STEMS`）は、同じ時点でレコードの画像一覧から外す。いまの対象は、NumberTales 61 のアイドル衣装（衣装デザインが第三者によるもの）を描いた 3 枚。上流で衣装・画像の単位の指定ができるようになるまでの、このリポジトリ側の除外。

作品メタの共通色（獣爾騎兵の制服共通色 `$EnumDef_CommonColor`）は manifest に無く、どのキャラクターが使うかの記録も無いため、配色へ足さない。別作品のレコードからの外見の継承（豹変系女子の N が NumberTales の 0 を参照する設定）も manifest に展開されないため、要約には入らない。

## 5 段階

| Stage | すること | 使うサービス |
| --- | --- | --- |
| 1 | キャラクターと形状の仕様からプロンプトを組む（同じ入力なら同じプロンプト） | なし |
| 2 | 公式設定画を観察して、見えている特徴を言葉にする。失敗時は続行／中止を確認し、無回答は中止 | OpenAI（`GPT_MODEL`） |
| 3 | ラフを生成する（単体 3 枚、合同は 1 体あたり 2 枚） | Gemini / OpenAI の画像モデル |
| 4 | ラフを検査する。形状と違反の指摘が 3 件以上なら作り直し、それ未満はラフを起点に修正する | OpenAI（`GPT_MODEL`）+ 画像モデル |
| 5 | 検査を通ったラフから完成画像を清書し、Canva の連携があれば仕上げて書き出す。合同では、完成画像で識別記号（番号・ラベル・タグなど）が持ち主以外に描かれていないかを見て、指摘を `run_meta.json` の `stages.stage5.mixing`（コネクタの結果では `mixing`）に残す。1 体では、清書した完成画像をラフと同じ観点で検査し、指摘を `stages.stage5.inspection`（コネクタの結果では `final_inspection`）に残す。形状の指摘（「絵の描かれた板」など）が出た清書は 1 回だけやり直し、指摘が減ったほうを完成画像にする（経過は `stages.stage5.retries`、コネクタの結果では `final_retries`）。形状以外の指摘と、合同の指摘は、直さずに記録する | 画像モデル + Canva（合同の検査は OpenAI） |

Stage 4 では、尾の本数と体数を「設定を知らせない別の呼び出し」で数えさせ、設定との照合をコードで行う。数え方には 1〜2 本の誤差がある。検査に失敗したラフは未検査と記録して通す。

## 状態と衣装の指定

変形前の姿の状態と衣装を選べる（CLI は `--state`・`--costume`、合同は `--states`。コネクタは `state`・`costume`、合同は `subjects[].state`）。

| 指定 | 扱い |
| --- | --- |
| DB にある状態の名前（`afterChanged`、`beasted` など） | その状態の項目を描く。Stage 4 もその状態の特徴で検査する。その状態に尾の本数が無ければ、本数の照合を省く |
| DB にある衣装の名前（`miko`、`otaku`、`jacketless` など） | その衣装の項目を描く。省くと `usual` |
| 名前に当たらない文 | 自由な指定として受ける。変えてよいのは体型と衣装・着用物だけで、耳・尾・髪・瞳・番号は設定のまま。状態の文で外せる着用物は、DB で `Detachable` の印が付いた品目だけ |

- 選べる名前は、`--dry-run` の `[INFO] … の選べる状態 / 選べる衣装`、コネクタの `shapechange_list_characters`（`states`・`costumes`）と `dry_run`（`selectable_states`・`selectable_costumes`）に出る。
- 状態の文は、条件付きの値の選び方にも効く（67 の身長は、文に「筋装備」があれば 190 cm、無ければ 145 cm）。
- 公式の参照画像は通常の状態・通常衣装の姿なので、プロンプトと検査に「体型と衣装が指定と食い違うときは指定を採る」「指定で変えた体型と衣装は違反に数えない」と添える。衣装ごとの参照画像の選び分けは、上流の画像の区分（CreationsDB #63）が入ってから。
- 第三者がデザインした衣装（NumberTales 61 の `idol`）は選べない。
- 表情の切り替えは、これまでどおり `--scene` や `--extra` で指定する。

プロンプトの `[参照画像の扱い]` は、その呼び出しで実際に添付する画像の並びに合わせて、ステージごとに組み直す（修正では「参照1: 修正対象のラフ」、清書では「参照1: 検査を通ったラフ」から始まる）。

## 入力画像

依頼者の画像を、役割を付けて渡せる（CLI は `--images`、コネクタは各生成ツールの `images`）。役割ごとに、枚数の上限と渡すステージが決まっている。

| 役割 | 意味 | 枚数 | Stage 3（ラフ） | Stage 4（検査と修正） | Stage 5（清書） |
| --- | --- | --- | --- | --- | --- |
| `base` | 加筆の起点 | 1 | ○（先頭） | × | ○ |
| `markup` | `base` に赤で指示を描き込んだ画像（赤入れ）。`base` か `--iterate-from` が必要 | 1 | ○ | ○（直ったか、印が残っていないか） | × |
| `composition` | 構図参考。ポーズ・配置・画角だけを借りる。自動でグレースケールにする | 1 | ○ | × | × |
| `reference` | 生成画像と関係のない参考。`note`（何の参考にするか）が必須 | 3 | ○ | × | ○ |
| `object` | 物品化の変形先の参考（下の「物品化」）。`--object` のときだけ | 1 | ○ | ○（物として読めるか） | ○ |

- どの役割の画像よりも、公式画像と設定資料を優先する（プロンプトにそう書く）。`reference` は `note` の用途にだけ使わせる。作風を借りたいときは、画像ではなく `--style` の文で伝える。
- 出所（`provenance`）は 1 枚ごとに必須: `own_generated`（このパイプラインの生成結果）／`own_work`（依頼者自身の作品）／`own_photo`（依頼者自身が撮った写真）。第三者の作品は受け付けない（[AGENTS.md](../AGENTS.md) の「著作物の扱い」）。
- 役割 `object` の画像に限り、出所に `cc0`（CC0 の素材）と `public_domain`（パブリックドメインの素材）も使える。`source_url`（出典の URL）と `license`（ライセンスの表記。例 `CC0 1.0`）が必須で、`run_meta.json` に残す。
- 1 枚 10MB まで、PNG・JPEG・WebP（拡張子ではなく中身で判定する）。赤入れの画像は、加筆の起点と縦横比が 2% 以上違うと受け付けない。
- 置き場は、CLI では `references/` か `outputs/` の中（`path`）、コネクタでは GCS の自分の接頭辞の下（`object_key`）。base64・Drive のファイル ID・任意の URL は受け付けない。
- 入力画像は実行フォルダーの `inputs/` に写し（構図参考はグレースケールにしたもの）、役割・出所・SHA-256 を `run_meta.json` の `input_images` に残す。
- `--ref` は従来どおり使える。`outputs/` の中の画像は加筆の起点、`references/` の中の画像は手元の追加資料として、Stage 3 と Stage 5 に渡す（Stage 4 には渡さなくなった）。
- 合同生成（`--joint`）では、1 体ずつのラフを場面なしで描くため、入力画像は全員を描く清書（Stage 5）でだけ使う。赤入れが反映されたかの検査は行わない。

```sh
# references/ に置いた自分のラフを構図参考にする（先に --dry-run で確認）
python -m src.pipeline.shape_pipeline --badge 57 --mode 4 --dry-run \
    --images references/images.json
```

`references/images.json` の例（PowerShell では JSON を引数に直接書くと引用符が崩れるので、ファイルにする）:

```json
[
  {"role": "composition", "path": "references/my-pose-sketch.png", "provenance": "own_work"},
  {"role": "reference", "path": "references/tile-floor.jpg", "provenance": "own_photo", "note": "床のタイルの質感"}
]
```

## 物品化

`--mode` の代わりに `--object "物の名前"` を渡すと、家具や道具などの物へ変わった姿を描く（コネクタは `object`）。形状番号の変形と違い、寸法を持たない。対応するのは 1 体・1 枚絵（`--layout single`、変形完了）だけで、`--joint`・`--amount`・`--size` とは組み合わせられない。

| 手がかり | 指定 | 既定 |
| --- | --- | --- |
| 配色と模様 | 髪・肌・衣装の色と柄を、物の表面へ移す。番号や紋章は、元の位置に当たる場所へ元の大きさのまま移す（手がかりに `number` を選ぶと、読める位置に描く。下の「構図と見分ける手がかり」） | 必須（選べない） |
| 顔 | `--face keep`（物の表面に顔を 1 つ描く）/ `none`（描かない） | `keep` |
| 腕と脚 | `--arms` / `--legs` の `keep`（物の外へ出す）/ `fold`（物の中へ収め、色だけが表面に見える）/ `hide`（描かない） | `fold` |
| 耳と尾 | `--appendages` の `keep`（元の形と本数で外へ出す）/ `integrate`（表面の絵柄にする） | `keep` |

- 変形先の参考画像は、入力画像の役割 `object` で渡せる（任意、1 枚）。形・構造・素材感の参考としてだけ使い、画像の中のロゴ・文字・キャラクターの柄・配色は写さないようにプロンプトで指示する。配色と模様は、公式画像のキャラクターから取る。
- Stage 4 の検査は、名前（と参考画像）のとおりの物として読めるか、人の体の輪郭が残りすぎていないかを `shape_issues` に、配色・模様と顔の扱いの食い違い、参考画像からの写し込みを `violations` に挙げる。尾の本数の照合は `--appendages keep` のときだけ行い、体数は数えない。作り直しの線（`shape_issues` と `violations` の合計が 3 件以上）は形状の変形と同じ。
- 実行フォルダー名は `…_object_<キャラクター>`、`run_meta.json` の `subjects[].shape` は `kind: "object"` と `label`（`物品化「名前」`）を持つ。

```sh
python -m src.pipeline.shape_pipeline --badge 57 --object "シャチ型の浮き輪" --dry-run
python -m src.pipeline.shape_pipeline --badge 57 --object "花瓶" --face none --appendages integrate --dry-run
```

## 構図と見分ける手がかり

構図は `--composition` で選ぶ（コネクタは `composition`）。対応するのは 1 体の生成だけで、`--joint` とは一緒に使えない。

| 値 | 描き方 | 使える範囲 |
| --- | --- | --- |
| `showcase`（既定） | 形が分かるように見せる。床に置き、接地影を付ける。寸法は変形前の身長に対する実寸（cm）で渡す | すべてのレイアウト |
| `scene` | `--scene` に書いた場面へ溶け込ませる。場面の物と重なる・挟まる・載る・浮かぶ。輪郭の 3 分の 1 くらいまでは隠れてよい | `--layout single`・変形完了だけ。`--scene` が必須 |

- `scene` では、寸法を「形の縦横比と厚み」だけで渡す（cm は渡さない）。場面の物との大きさの関係は `--scene` の文で指定する（「トランプと同じ大きさ」など）。固定の「床に置く・接地影」は使わない。
- 同じ種類の物が周りにある場面（カードの山など）では、周りの物をその物のふつうの柄で描き、キャラクターの配色・顔・番号を写さないように指示する。変わった姿は絵の中に 1 つだけ描かせる（表と裏を別の物として並べない）。
- 薄い形（コイン、人型平面、低い円盤、板、反物。最も短い辺が身長の 5% 以下）は、構図によらず「体そのものが薄い面になる。全身の絵を印刷したカード・ポスター・看板にしない」と伝える。Stage 4 の検査は、面の中に小さな全身像・余白・背景・枠があれば「絵の描かれた板: 〜」として挙げる。
- 自然文の入口（`--natural`）は、「混ざっている」「埋もれている」「浮かんでいる」のような言い方から `scene` を読み取る。1 体・1 枚絵で、場面が読み取れたときだけ `scene` にする。

見分ける手がかりは `--cues` で選ぶ（コネクタは `cues`。カンマ区切り）。「この絵で、どこを見ればその子だと分かるか」を、プロンプトと Stage 4 の検査の両方に同じ一覧で渡す。

| 語 | 手がかり |
| --- | --- |
| `face` | 顔（顔立ちと瞳の色） |
| `palette` | 配色（髪・肌・衣装の色と柄） |
| `number` | 番号・紋章 |
| `ears` / `tail` | 耳 / 尾（種類と本数） |
| `hair` | 髪のモチーフ（髪型の特徴・髪飾り） |
| `outfit` | 衣装の品目（目印になる服や小物） |

- 省いたときの既定: `palette` と、顔を描く形なら `face`、耳と尾の形を保つ指定（`--appendages keep`）なら `ears` と `tail`。`number` は既定に入れない。
- 番号と紋章: `number` を選んだときだけ、読める位置に設定の字形で描く。選ばないときは、設定の位置・大きさ・個数のまま扱う。設定で「隠れている・ふだん見えない」とされる番号は描かず、見えている番号は設定の位置に 1 つだけ描く。参照画像の題字（名前の横の大きな番号）は写さない。検査も、隠れている番号が見えないことは指摘せず、設定より大きい・目立つ場所へ移っている場合を「番号: 〜」として挙げる。
- `--cue-note` に、語で言えない手がかりを文で足せる（「前髪のハート」など）。
- Stage 4 の検査: 選んだ手がかりが見えない・隠れている・崩れているときは「手がかり: 〜」。`scene` では、手がかりが場面のほかの物にも描かれていれば「取り違え: 〜」。`scene` の尾の本数は `tail` を選んだときだけ照合し、体数は数え上げでは照合しない（周りの同じ種類の物まで数えられるため）。代わりに、変わった姿が 2 つ以上描かれていれば「体数: 〜」として挙げる。
- `run_meta.json` には、`composition` と、`subjects[].shape` の `composition`・`cues`・`cue_note` が残る。

```sh
python -m src.pipeline.shape_pipeline --badge 58 --mode 14 --composition scene --scene "机の上に崩れたトランプの山。その中の 1 枚として混ざっている。トランプと同じ大きさ" --dry-run
python -m src.pipeline.shape_pipeline --badge 57 --mode 4 --cues face,palette,number --cue-note "前髪のハート" --dry-run
```

## 使い方

```sh
pip install -r requirements.txt

# 形状の一覧と寸法 / AI 利用が許可されたキャラクターの一覧
python -m src.pipeline.shape_pipeline --list-modes
python -m src.pipeline.shape_pipeline --list-characters --work NumberTales

# プロンプトと参照画像の確認（生成しない・課金なし）
python -m src.pipeline.shape_pipeline --badge 57 --mode 4 --dry-run

# 5 段階で生成 / 検査なしで 1 回だけ生成
python -m src.pipeline.shape_pipeline --badge 57 --mode 4
python -m src.pipeline.shape_pipeline --badge 57 --mode 4 --quick

# 複数キャラクターを 1 枚に（キャラクターごとに形状を指定）
python -m src.pipeline.shape_pipeline --joint 85:0,73:3 --scene "公園の芝生の上"

# 自然文の依頼（読み取ったキャラクターと形状を表示してから進む）
python -m src.pipeline.shape_pipeline --natural "85(ハッコ)が箱化している様子" --dry-run

# 改稿（以前の完成画像を起点に、変える点を指定してやり直す）
python -m src.pipeline.shape_pipeline --badge 57 --mode 4 \
    --iterate-from 20261118_130500_gemini_mode04_NumberTales-57 --revisions "尾を 7 本そろえる"

# 軽い加筆（起点の画像の編集 1 回だけ。観察・検査を省く）
python -m src.pipeline.shape_pipeline --badge 57 --mode 4 --quick --skip-canva \
    --iterate-from 20261118_130500_gemini_mode04_NumberTales-57 --revisions "表情を笑顔にする"
```

改稿の実行フォルダーは、名前の末尾に `_iter1`、その改稿をさらに改稿すると `_iter2` が付く。`--quick` の改稿は、起点の実行が `run_meta.json` の `design_observations` に残した Stage 2 の観察を使い回す（観察をやり直さない）。変える点は `--revisions` の文か、赤入れの画像（役割 `markup`）で伝え、両方あれば両方使う。

生成は課金を伴う。先に `--dry-run` でプロンプトと参照画像を確認する。5 段階は 1 回の実行で、画像生成が最大 7 回ほど（ラフ 3、修正最大 3、完成 1）と Vision の呼び出しが 7 回ほどかかる（合同は、完成画像 1 枚につき 1 回増える）。

PowerShell で `python` が Microsoft Store のエイリアスに当たって起動しない場合は、`python` を `py` に読み替える（例: `py -m src.pipeline.shape_pipeline --list-modes`）。

### 主なオプション

| オプション | 内容 |
| --- | --- |
| `--badge` / `--name` / `--work` | キャラクターの指定。`--badge` は CreationsDB のバッジ（`57`, `2B`, `EZ1`, `ENE`）。作品コード付き（`NTS-57`, `SAR-EZ1`）なら `--work` は要らない。番号と作品内の識別子（`2-alt`, `Mouse`）も使える。`--name` は名前の一部。バッジは `--list-characters` の 2 列目に出る |
| `--joint` | `ID:形状番号` を 2〜4 件（例 `85:0,73:3` / `SAR-EZ1:0,SAR-EZ4:3`）。ID はバッジ・番号・名前の一部 |
| `--natural` | 自然文の依頼。複数に読める形状語は代表の形状に決め打ちする（表は `pipeline/natural_parser.py`） |
| `--form` | 変形前の形態（`humanoid` / `corefolder`。形態を持つ作品のみ） |
| `--state` / `--costume` / `--states` | 変形前の姿の状態と衣装（上の「状態と衣装の指定」）。`--states` は `--joint` の各キャラクターの状態を `;` 区切りで並べる |
| `--mode` | 形状番号 0〜15 |
| `--object` / `--face` | 物品化（上の「物品化」）。`--mode` の代わりに変形先の物の名前を渡す。`--face` は顔を残すか（`keep` / `none`） |
| `--layout` | `single`（1 体）/ `sequence`（変形前→途中→完了）/ `five-view`（五方向図） |
| `--amount` | 変形量。1 未満で途中の姿（`single` のみ） |
| `--arms` / `--legs` | `keep` / `fold` / `hide`。形状 0〜8 と物品化で選べる |
| `--appendages` | 耳と尾を `keep`（形を保つ）/ `integrate`（形状へ統合）。形状 0〜8 と物品化で選べる |
| `--size` | 形状全体の大きさ（0.2〜2） |
| `--scene` / `--style` / `--extra` | 場面・画風・追加の作画指定 |
| `--composition` | `showcase`（形を見せる。既定）/ `scene`（`--scene` の場面に溶け込ませる）。上の「構図と見分ける手がかり」 |
| `--cues` / `--cue-note` | 見分ける手がかりの語（カンマ区切り）と、文での補足。1 体の生成だけ |
| `--ref` | 追加の参照画像。`references/` か `outputs/` の中のファイルに限る |
| `--images` | 役割を付けた入力画像（上の「入力画像」）。JSON の配列か、それを書いた `.json` ファイル |
| `--no-refs` | 公式画像を添付しない（Stage 2 の観察もできなくなる） |
| `--iterate-from` / `--revisions` | 改稿の起点（実行フォルダー名か `outputs/` の中の画像）と、変える点（`;` 区切り） |
| `--provider` | 画像モデルの提供元。`gemini`（既定）/ `openai` |
| `--count` / `--rough-count` | 完成画像の枚数（1〜4）/ ラフの枚数（1〜5） |
| `--quick` | 検査なしで 1 回だけ生成する。加筆の起点があれば、その画像の編集 1 回になる。`--joint` では加筆の起点があるときだけ使える |
| `--skip-canva` | Canva 仕上げを省く |
| `--height-cm` | 身長を上書きする（DB に身長が無いキャラクター向け） |

### 環境変数

`.env`（[.env.example](../.env.example)）から読む。

- 画像モデル: `GEMINI_API_KEY` と `GEMINI_IMAGE_MODEL`（未設定なら `GEMINI_REFERENCE_MODEL`）、`OPENAI_API_KEY` と `OPENAI_IMAGE_MODEL`。
- 検査: `OPENAI_API_KEY` と `GPT_MODEL`（既定 `gpt-4o`）。
- Canva: `SHAPECHANGE_CANVA_ACCESS_TOKEN`（取得と更新は下の「Canva の連携」）。GeneratorsAI の `CANVA_ACCESS_TOKEN` は読まない。Canva のリフレッシュトークンは使うたびに入れ替わるので、連携を共有すると片方が失効する。

### Canva の連携

Stage 5 の Canva 仕上げは、ShapeChangerAI 用に作った Canva の連携で行う。トークンの取得と更新の方法は GeneratorsAI と同じ（OAuth 2.0 の PKCE とリフレッシュトークン）で、環境変数は `SHAPECHANGE_CANVA_*` に分けている。連携はまだ作っていないので、この手順は実際の Canva API では確かめていない（トークンのやり取りは偽物でテストしている）。

1. Canva の Developer Portal で連携を作る（[Canva Connect](https://www.canva.dev/docs/connect/)）。Redirect URL に `http://127.0.0.1:3001/oauth/redirect` を登録し、スコープは `asset:read`・`asset:write`・`design:content:read`・`design:content:write`・`design:meta:read` を有効にする。
2. 連携の Client ID と Client Secret を、`.env` の `SHAPECHANGE_CANVA_CLIENT_ID` と `SHAPECHANGE_CANVA_CLIENT_SECRET` に入れる。
3. トークンを取得する。表示された URL をブラウザで開いて許可すると、`.env` の `SHAPECHANGE_CANVA_ACCESS_TOKEN` と `SHAPECHANGE_CANVA_REFRESH_TOKEN` が書き込まれる。トークンの値は画面に出ない。

```sh
python -m src.tools.refresh_canva_token                        # 初回: ブラウザで許可して取得する
python -m src.tools.refresh_canva_token --use-refresh-token    # 2 回目以降: ブラウザなしで更新する
```

アクセストークンは短い時間で失効する。失効していると Canva 仕上げだけが省かれ、完成画像は Canva を通さないものになる（理由は `run_meta.json` の Stage 5 の記録に残る）。そのときは `--use-refresh-token` か、コネクタの `shapechange_refresh_canva_token` で更新してから生成し直す。コネクタのツールは、新しいトークンを `.env`（あれば）と Secret Manager（使えれば）へ保存し、保存できた場所を結果の `saved_to` に返す。Secret Manager では、新しい版を足したあとに 1 つ前の版を消す（入れ替わる前のトークンはもう使えず、版は残っているだけで課金されるため）。

リフレッシュトークンは 1 回使うと新しいものに入れ替わる。PC の `.env` と Secret Manager が同じトークンを持っているとき、片方で更新すると、もう片方のリフレッシュトークンは使えなくなる。使えなくなったら、PC で引数なしの取得からやり直し、Cloud Run で使う分は登録し直す（[docs/deploy-cloud-run.md](../docs/deploy-cloud-run.md) の「Canva の連携」）。

## 出力

```text
outputs/{YYYYMMDD_HHMMSS}_{provider}_mode{NN}_{作品}-{識別子}/
├── prompt.txt                      実際に使ったプロンプト（Stage 2 の観察を足したもの）
├── run_meta.json                   日時・モデル・対象・形状と寸法・参照元・各 Stage の結果・元の引数
├── notes.md                        確認結果と再試行で見直す点（手書き用）
├── inputs/base_01.png など         入力画像の写し（指定したときだけ。構図参考はグレースケール）
├── stage2_reference/observation.json   観察結果、または失敗理由と回答
├── stage3_rough/rough_NN.png
├── stage4_correct/analysis_log.json    ラフごとの指摘・数えた本数・処置
├── stage4_correct/rough_NN_corrected_01.png / rough_NN_regen_01.png
└── stage5_final/mode04_….png       完成画像（Canva 仕上げは stage5_final/canva/）
```

改稿は実行フォルダー名の末尾に `_iterN` が付く。合同生成は `…_joint_{対象}_{対象}/` に、キャラクターごとの `char_{対象}/stage2〜4` と、全員を描いた `stage5_final/joint_01.png` を置く。`--quick` は実行フォルダー直下に画像を置く。3 つの記録は生成 API を呼ぶ前に作るので、失敗時もプロンプトと条件が残る。`outputs/` は Git の追跡対象外。

## コネクタ（MCP サーバー）

```sh
pip install -r requirements-mcp.txt
python -m src.mcp_server            # ローカル（stdio）
```

Claude Desktop などに登録する例（パスは環境に合わせる）:

```json
{
  "mcpServers": {
    "shapechange": {
      "command": "py",
      "args": ["-m", "src.mcp_server"],
      "cwd": "C:\\Visual Studio Code UserFile\\100BeautiesLab_ShapeChangerAI",
      "env": { "MCP_TRANSPORT": "stdio", "OUTPUT_SINK": "local", "PYTHONUTF8": "1" }
    }
  }
}
```

| ツール | 内容 |
| --- | --- |
| `shapechange_generate_character` / `shapechange_generate_joint` | 単体・合同の生成。ジョブを登録して `job_id` を返す。`images` で入力画像を渡せる。単体は、`mode` の代わりに `object`（と `face`）を渡すと物品化になる。単体は `composition`（`scene` で場面に溶け込ませる）と `cues`・`cue_note`（見分ける手がかり）も渡せる |
| `shapechange_generate_from_natural` | 自然文の依頼から生成。読み取り結果は結果の `interpretation` に入る。入力画像は渡せない |
| `shapechange_iterate` | 以前の実行と同じ指定で、その完成画像を起点に改稿する。変える点は `revisions` か赤入れの画像（`images` の `markup`）で伝える。`quick` は起点の画像の編集 1 回だけ |
| `shapechange_create_upload_slot` | 入力画像 1 枚の受け取り口。署名付き PUT URL（15 分有効）と `object_key` を返す |
| `shapechange_job_status` | 進捗と完成画像のリンク。`awaiting_confirmation` なら利用者に確認する |
| `shapechange_answer_reference_warning` | Stage 2 の観察失敗に対する続行／中止の回答（無回答は 30 分で中止） |
| `shapechange_list_runs` / `shapechange_get_run_logs` | 実行の一覧と、プロンプト・検査ログ・中間画像 |
| `shapechange_list_gcs_logs` / `shapechange_get_gcs_image` | GCS に残っている中間画像の一覧（署名付き URL 付き）と、1 件の取得（base64） |
| `shapechange_refresh_canva_token` | Canva のアクセストークンの更新（上の「Canva の連携」） |
| `shapechange_list_modes` / `shapechange_list_characters` | 指定できる形状とキャラクター |

生成の 4 ツールは `dry_run: true` を受け付ける。ジョブを登録せず、プロンプト、役割の付いた画像の一覧（渡すステージ付き）、生成しうる画像の枚数の上限をその場で返す。画像の生成は呼ばない（自然文のツールだけは、依頼文の読み取りに OpenAI の API を使う）。

コネクタへ画像を渡す手順:

1. `shapechange_create_upload_slot` に形式（`image/png`・`image/jpeg`・`image/webp`）を渡す。
2. 返ってきた `upload_command` のとおりに、`headers` を付けて `upload_url` へ PUT する（`curl --upload-file`）。
3. `object_key` を、役割と出所と一緒に生成ツールの `images` に入れる。`shapechange_list_gcs_logs` が返す中間画像の `object_key` も、そのまま使える（出所は `own_generated`）。

受け取った画像は GCS の `<GCS_PREFIX>/uploads/<日付>/` に置く。読めるのは自分の接頭辞の下だけで、同じバケットのほかの用途の画像は読まない。7 日で消す設定は、バケットのライフサイクルで行う（[docs/deploy-cloud-run.md](../docs/deploy-cloud-run.md)）。

完成画像は `OUTPUT_SINK`（`local` / `drive` / `gcs`）へ届ける。Drive では `DRIVE_FOLDER_ID` の直下に `shapechange_{実行名}__…` の名前で置く。中間画像は Drive へ上げず、`GCS_BUCKET` があれば GCS、無ければサーバー内に残す。ジョブはメモリー上にあり、サーバーを再起動すると消える（実行の記録は `outputs/` に残る）。

`OUTPUT_SINK` が `local` 以外で `GCS_BUCKET` があれば、実行の `run_meta.json` と完成画像を GCS の `<GCS_PREFIX>/runs/<run_id>/` にも置く。Cloud Run のインスタンスが止まって `outputs/` が消えたあとも、`shapechange_list_runs` に `location: gcs` として並び、`shapechange_iterate` の起点にできる（そのときに手元へ戻す）。`shapechange_get_run_logs` は、戻せた `run_meta.json` だけを返す（プロンプトと検査ログは戻らない）。

GCS に置いた中間画像は、`shapechange_list_gcs_logs` で後から一覧できる。バケットを直接見るので、サーバーが再起動して `outputs/` が消えたあとも使える。GeneratorsAI の `numbertales_list_gcs_logs`・`numbertales_get_gcs_image` と同じ作りで、違いは 2 つ。取得できる画像を `GCS_PREFIX`（既定 `shapechange`）の下に限っていることと、一覧の既定の件数が 50 件であること（GeneratorsAI は 300 件）。

VM では、GeneratorsAI と同じく、使うときだけ起動して SSH 越しの stdio でつなぐ。配置と更新の手順は [docs/deploy-vm.md](../docs/deploy-vm.md)。

claude.ai のコネクタとして使う公開の配置は Cloud Run にある。設定・配置・登録の手順は [docs/deploy-cloud-run.md](../docs/deploy-cloud-run.md)。

HTTP で公開する場合は `MCP_TRANSPORT=streamable-http`・`MCP_ISSUER_URL`・`MCP_AUTH_PASSPHRASE` を設定する。認証は OAuth で、コネクタの接続のたびに合言葉の確認画面（`/consent`）を通す。合言葉が未設定なら接続をすべて断り、`MCP_ISSUER_URL` が無ければ起動を断る（手元の検証で認証なしにする場合だけ `MCP_ALLOW_UNAUTHENTICATED=1`）。合言葉を知っている人を同じ利用者として扱い、利用者ごとの本人確認はしない。VM に常駐させて公開する形は見送った。

VM は、公開用のシェーダーパッケージをサブモジュールとして取得する。Cloud Run のイメージには、次のコマンドが空のフォルダーへ書き出すシェーダーの 2 ファイルだけを入れ、`SHAPECHANGE_REPO_PATH` で置き場所を指す（[deploy/deploy_cloud_run.py](../deploy/deploy_cloud_run.py) が行う）。

```sh
python -m src.shape.vm_bundle outputs/vm-shape-data
```

## 構成

| ファイル | 役割 |
| --- | --- |
| `pipeline/shape_pipeline.py` | CLI と実行本体（計画 → 5 段階 → 記録） |
| `pipeline/prompt_builder.py` | プロンプトの組み立て（単体・合同）。参照画像の役割ごとの使い方 |
| `pipeline/input_images.py` | 入力画像の役割・検証・ステージへの振り分け・`inputs/` への書き出し |
| `pipeline/design_reference.py` | Stage 2: 公式設定画の観察と確認待ち |
| `pipeline/correction_generator.py` | Stage 4: 検査と修正 |
| `pipeline/final_generator.py` | Stage 5: 清書と Canva 仕上げ |
| `pipeline/natural_parser.py` | 自然文の依頼の読み取り |
| `shape/vrc_source.py` / `shape/modes.py` | シェーダーからの寸法の読み取り、形状仕様と体格の計算 |
| `shape/objects.py` | 物品化の仕様（顔・腕と脚・耳と尾の扱い。寸法は持たない） |
| `shape/cues.py` | 構図（`showcase` / `scene`）と、見分ける手がかりの語・既定 |
| `shape/vm_bundle.py` | Cloud Run のイメージへ渡すシェーダー 2 ファイルの書き出し |
| `utils/dataset.py` | manifest の読み込み、AI 利用ゲート、キャラクター検索、参照画像の選定 |
| `gemini/generate.py` / `openai/generate.py` / `openai/vision.py` / `canva/generate.py` | 外部 API の呼び出し |
| `tools/refresh_canva_token.py` | Canva の連携のトークンの取得・更新と、Secret Manager への登録 |
| `mcp_server/` | MCP サーバー。`tools.py` が中身、`server.py` が MCP への登録。`jobs.py` / `auth.py` / `output_sink.py` は GeneratorsAI から移したもの。`state_store.py` は、サインインの状態とジョブの記録を GCS にも置いて、インスタンスが入れ替わっても残す |
| `utils/run_log.py` / `utils/paths.py` / `utils/image_io.py` / `utils/provider.py` | 記録・実行フォルダー・画像の保存・共通の型 |

## テスト

```sh
python tests/test_shape_source.py    # シェーダーからの寸法の読み取り
python tests/test_dataset_gate.py    # キャラクター検索と AI 利用ゲート、衣装と画像の除外
python tests/test_pipeline.py        # 5 段階・合同・改稿・自然文・記録
python tests/test_input_images.py    # 入力画像の役割と検証、ステージへの振り分け、軽い加筆、受け取り口、GCS からの復元
python tests/test_providers.py       # 外部 API の呼び出し方（SDK は偽物に差し替え）
python tests/test_mcp_tools.py       # MCP ツールの中身（MCP SDK は不要）
python tests/test_canva_token.py     # Canva の連携のトークンの取得・更新（Canva と GCP は偽物に差し替え）
python tests/test_paths.py           # 実行フォルダーの作成
```

生成 API は呼ばない。サブモジュールを更新した後はすべて実行する。
