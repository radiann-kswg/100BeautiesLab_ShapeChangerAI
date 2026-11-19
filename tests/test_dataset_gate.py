"""キャラクターデータの読み取りと AI 利用ゲートの回帰テスト。

    python tests/test_dataset_gate.py
    python -m pytest tests/

合成レコードでゲートの規則を、実 manifest で許可・拒否レコードの扱いを確認する。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.utils import dataset  # noqa: E402


def test_gate_is_fail_closed():
    assert dataset.generation_gate({"ai_training": {"allowed": True, "reason": "ok"}})["allowed"] is True
    assert dataset.generation_gate({"ai_training": {"allowed": False, "reason": "AI_Optout"}})["allowed"] is False
    # 欠落・不明・真偽値以外は許可しない
    assert dataset.generation_gate({})["allowed"] is False
    assert dataset.generation_gate(None)["allowed"] is False
    assert dataset.generation_gate({"ai_training": {"allowed": "true"}})["allowed"] is False


def test_find_allowed_character_by_badge_and_name():
    izuna = dataset.find_character(badge=57)
    assert izuna["work_key"] == "#Works_NumberTales" and dataset.character_slug(izuna) == "NumberTales-57"
    scoped = dataset.find_character(badge="57", work_key="NumberTales")   # 作品キーは表記ゆれを許す
    assert dataset.character_slug(scoped) == "NumberTales-57"
    alt = dataset.find_character(badge="2B")       # Num_Badge → Num "2-alt"
    assert dataset.identity_label(alt) == "2-alt"
    # 2026-11-19 の上流で #DB_Regioministrators が許可され、スフィリアは 2 レコードになった。名前だけでは絞れない
    sphiria = dataset.find_character(badge="SCG-ENE")
    assert dataset.character_slug(sphiria) == "SinisterChangingGirls-ENE"


def test_characters_are_found_by_their_index_badge():
    # バッジは作品別 db_type.json の $IndexDef.$badge の宣言どおり（Num_Badge / 辞書の Beast_Badge / 方位そのまま）
    mouse = dataset.find_character(badge="EZ1", work_key="ShauErRiders")
    assert dataset.character_slug(mouse) == "ShauErRiders-Mouse" and dataset.full_badge(mouse) == "SAR-EZ1"
    # 作品コード付きなら作品の指定は要らない。大文字小文字は問わない
    for badge, slug in (("sar-ez1", "ShauErRiders-Mouse"), ("NTS-2B", "NumberTales-2-alt"),
                        ("SCG-ENE", "SinisterChangingGirls-ENE"), ("NTS-57", "NumberTales-57")):
        assert dataset.character_slug(dataset.find_character(badge=badge)) == slug
    # 番号と作品内の識別子も引き続き使える（ハイフン入りの番号を作品コードと取り違えない）
    assert dataset.character_slug(dataset.find_character(badge="2-alt")) == "NumberTales-2-alt"
    assert dataset.character_slug(dataset.find_character(badge="Mouse", work_key="ShauErRiders")) == "ShauErRiders-Mouse"
    assert dataset.character_slug(dataset.find_any("SAR-EZ4")) == "ShauErRiders-Rabbit"
    # 許可されたキャラクターは全員、作品コード付きのバッジ 1 語で本人に届く
    # 識別子 "W" は DB をまたいで重なる。バッジ "W" は #DB_Primary、"AW" は #DB_Regioministrators
    assert dataset.find_character(badge="SCG-W")["db_key"] == "#DB_Primary"
    assert dataset.find_character(badge="SCG-AW")["db_key"] == "#DB_Regioministrators"
    for record in dataset.get_characters():
        found = dataset.find_character(badge=dataset.full_badge(record))
        assert dataset.character_slug(found) == dataset.character_slug(record), dataset.full_badge(record)


def test_denied_and_ambiguous_lookups_do_not_return_records():
    denied = [r for r in dataset.load_manifest()
              if dataset.is_character(r) and not dataset.generation_gate(r)["allowed"]]
    assert denied, "拒否設定のレコードが manifest にある前提"
    target = next(r for r in denied if (r.get("data") or {}).get("Name_JP"))
    name = str(target["data"]["Name_JP"]).split("\n")[0]
    for query in (dict(name=name, work_key=target["work_key"]), dict(name="扇"), dict(name="スフィリア"), dict(badge="no-such-badge")):
        try:
            dataset.find_character(**query)
        except dataset.CharacterLookupError:
            continue
        raise AssertionError(f"特定できないはずの検索が通った: {query}")
    listed = {id(r) for r in dataset.get_characters()}
    assert not any(id(r) in listed for r in denied)


def test_summary_and_references_use_only_the_target_character():
    izuna = dataset.find_character(badge=57)
    summary = dataset.summarize_character(izuna)
    assert summary["form"] == "humanoid" and summary["height_cm"] == 152.0
    assert any("7 tails" in line for line in summary["tails"])
    assert any("57" in line for line in summary["appearance_details"])
    refs = dataset.collect_reference_images(izuna, summary["form"])
    data_root = (dataset.creations_db_root() / "data").resolve()
    assert 1 <= len(refs) <= 4
    for ref in refs:
        assert ref["path"].is_file() and data_root in ref["path"].parents
        assert "NTS-57" in ref["path"].name          # 本人の画像だけ
    # 形態データの無い作品でも要約できる。身長の複数値は先頭の数値を採る
    sphiria = dataset.summarize_character(dataset.find_character(badge="SCG-ENE"))
    assert sphiria["form"] is None and sphiria["height_cm"] == 159.0
    # 文章の設定があるかどうかは原典の更新で変わるので、合成レコードで確かめる
    assert not dataset.summarize_character({"data": {}})["has_text_design"]
    assert dataset.summarize_character({"ai_hints": {"common": {}}})["has_text_design"]
    muna = dataset.find_character(badge=67)
    assert dataset.resolve_height_cm(muna) == (145.0, "通常時")
    # 拒否設定のレコードには参照画像を返さない
    assert dataset.collect_reference_images({**izuna, "ai_training": {"allowed": False}}, "humanoid") == []


def test_structurally_resynced_tags_override_older_form_descriptions():
    record = {"ai_hints": {"common": {
        "identity_tags": ["fox", "TODO: ear type"],
        "silhouette_features": ["small", " todo: silhouette"],
        "immutable_traits": ["keep tail count", "TODO: constraints"],
    }, "forms": {"humanoid": {
        "ai_tags": ["new coat", "TODO: outfit"],
        "silhouette_notes": {"body_description": ["old jacket", "TODO: body"],
                             "attached_items": ["old hat", "TODO: items"]},
        "outfit_features": ["old boots", "TODO: clothes"],
        "negative_keywords": ["wrong limbs", " NEW COAT ", "TODO: negatives"],
        "negative_visuals": ["other form outfit", "new coat"],
    }}}}
    summary = dataset.summarize_character(record, "humanoid")
    assert summary["form_features"] == ["new coat"]
    assert summary["negative_keywords"] == ["wrong limbs", "other form outfit"]
    assert summary["attached_items"] == []
    assert summary["identity_tags"] == ["fox"]
    assert summary["silhouette_features"] == ["small"]
    assert summary["immutable_constraints"] == ["keep tail count"]
    assert record["ai_hints"]["common"]["identity_tags"] == ["fox", "TODO: ear type"]
    assert record["ai_hints"]["forms"]["humanoid"]["ai_tags"] == ["new coat", "TODO: outfit"]
    del record["ai_hints"]["forms"]["humanoid"]["ai_tags"]
    assert dataset.summarize_character(record, "humanoid")["form_features"] == ["old jacket", "old boots"]
    assert dataset.summarize_character(record, "humanoid")["attached_items"] == ["old hat"]
    izuna = dataset.summarize_character(dataset.find_character(badge=57), "humanoid")
    assert "long olive-green skirt" in izuna["form_features"]
    assert "orange shorts" not in izuna["form_features"]
    assert "amber eyes" not in izuna["form_features"]
    one = dataset.summarize_character(dataset.find_character(badge=1), "humanoid")
    assert "light pink hooded top" in one["form_features"]
    assert "light pink hooded top" not in one["negative_keywords"]


def test_palette_notes_and_character_relative_sides_survive_summary():
    record = {"data": {
        "AppearanceDetail": [{"BodyPart": ["#BodyPart_Chest"], "Laterality": "#Lat_Left",
                              "Attrs": [{"value_EN": "number mark"}]}],
        "ColorPalette": [
            {"Hex": "#FFFFFF", "AppliesTo": ["#BodyPart_Hair"], "Note_EN": "clips only"},
            {"Hex": "#000000", "Formation": "humanoid", "Note_EN": "not this form"},
            {"Hex": "#FF0000", "Note_JP": "髪飾りだけ"},
        ],
    }}
    summary = dataset.summarize_character(record)
    assert "character's Left" in summary["appearance_details"][0]
    assert len(summary["palette"]) == 2
    assert "clips only" in summary["palette"][0]
    assert "髪飾りだけ" in summary["palette"][1]


def test_shape_codes_counts_notes_and_story_states_reach_the_summary():
    record = {"data": {"AppearanceDetail": [
        {"BodyPart": ["#BodyPart_Ear"], "Attrs": [
            {"AttrLabel": "#DesignAttr_Ear", "vdict_EarShapeType": "#EarShapeType_Mouse",
             "about_EN": "large and round"}]},
        {"BodyPart": ["#BodyPart_Tail"], "Note_EN": "usual uniform", "Attrs": [
            {"AttrLabel": "#DesignAttr_Shape", "vdict_TailShapeType": "#TailShapeType_Mouse"},
            {"AttrLabel": "#DesignAttr_Count", "value_Num": 1}]},
        {"BodyPart": ["#BodyPart_Wing"], "Attrs": [{"AttrLabel": "#DesignAttr_Count", "value_Num": 2}]},
        {"Formation": "beforeChange", "BodyPart": ["#BodyPart_Eye"], "Attrs": [{"value_EN": "orange"}]},
        {"Formation": "afterChanged", "BodyPart": ["#BodyPart_Tail"], "Attrs": [
            {"value_EN": "many tails"}, {"AttrLabel": "#DesignAttr_Count", "value_Num": 9}]},
        {"Formation": "corefolder", "BodyPart": ["#BodyPart_Eye"], "Attrs": [{"value_EN": "other form"}]},
    ]}}
    summary = dataset.summarize_character(record)
    ears, tail, wing, before, after = summary["appearance_details"]   # 別形態の項目は入らない
    assert ears == "Ear: Mouse (large and round)"
    assert tail == "Tail: Mouse; count: 1 (usual uniform)"
    assert wing == "Wing: count: 2"
    assert before == "Eye: orange"                                    # 既定の状態は通常の項目
    assert after.startswith("[別状態 afterChanged") and "many tails" in after
    assert summary["tail_count"] == 1                                 # 翼の本数と別状態の尾は数えない
    # 実データ: 獣爾騎兵は耳・尾を辞書コードと本数で、豹変系女子は豹変前後を Formation で持つ
    mouse = dataset.summarize_character(dataset.find_character(badge="Mouse", work_key="ShauErRiders"))
    assert mouse["tail_count"] == 1
    assert any("Mouse (large and round)" in line for line in mouse["appearance_details"])
    runo = dataset.summarize_character(dataset.find_character(badge="E", work_key="SinisterChangingGirls"))
    assert any(line.startswith("Eye") and "reddish orange" in line for line in runo["appearance_details"])
    fox = [line for line in runo["appearance_details"] if "Fox" in line]
    assert fox and all(line.startswith("[別状態 afterChanged") for line in fox)
    assert runo["tail_count"] is None                                 # 豹変後の尾は既定の本数にしない
    assert dataset.summarize_character(dataset.find_character(badge=57))["tail_count"] == 7   # TailsUnit が優先


if __name__ == "__main__":
    from _runner import run_module_tests

    run_module_tests(globals())
