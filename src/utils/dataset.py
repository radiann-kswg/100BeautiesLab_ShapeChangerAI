"""utils/dataset.py — CreationsAI の manifest からキャラクターデータを読む。

入力は ``_creations-ai/ai-dataset/manifest.jsonl``（読み取り専用）。
AI 利用可否は manifest の ``ai_training.allowed`` を読むだけで、判定を再実装しない
（正典は上流 ``_creations-ai/scripts/lib/policy.js``）。AGENTS.md「著作物の扱い」に従い、
``allowed`` が明示的に true のレコードだけを生成入力にする（欠落・不明は拒否）。
"""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path
from typing import Any

from .paths import project_root

FORMS = ("humanoid", "corefolder")


class CharacterLookupError(LookupError):
    """キャラクターを一意に特定できない、または AI 利用が許可されていない。"""


# ── パス ──────────────────────────────────────────────────────────────


def creations_ai_root() -> Path:
    return project_root() / "_creations-ai"


def creations_db_root() -> Path:
    return creations_ai_root() / "creations-db"


def manifest_path() -> Path:
    override = os.environ.get("MANIFEST_PATH")
    return Path(override) if override else creations_ai_root() / "ai-dataset" / "manifest.jsonl"


# ── 読み込みと AI 利用ゲート ──────────────────────────────────────────


def load_manifest() -> list[dict[str, Any]]:
    target = manifest_path()
    if not target.is_file():
        raise FileNotFoundError(
            f"manifest が見つかりません: {target}\n"
            "  git submodule update --init --recursive を実行してください。"
        )
    with target.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def is_character(record: dict[str, Any]) -> bool:
    """キャラクターレコードか。References 由来（種族・地域・用語など）は除く。"""
    source = str(record.get("db_source") or "").replace("\\", "/")
    return record.get("_type") == "character" and not source.startswith("data/References/")


def generation_gate(record: dict[str, Any] | None) -> dict[str, Any]:
    """``{"allowed": bool, "reason": str}`` を返す。allowed は明示的な true のみ通す。"""
    training = (record or {}).get("ai_training")
    if not isinstance(training, dict):
        return {"allowed": False, "reason": "ai_training が manifest に無いため、利用可否を確認できません。"}
    return {"allowed": training.get("allowed") is True, "reason": str(training.get("reason") or "")}


def get_characters() -> list[dict[str, Any]]:
    """AI 利用が許可されたキャラクターレコードだけを返す。"""
    return [r for r in load_manifest() if is_character(r) and generation_gate(r)["allowed"]]


# ── 識別と検索 ────────────────────────────────────────────────────────


def normalize_work_key(value: str | None) -> str | None:
    """"NumberTales" / "Works_NumberTales" / "#Works_NumberTales" を同じキーへ揃える。"""
    if not value:
        return None
    text = value.strip().lstrip("#")
    return "#" + (text if text.startswith("Works_") else f"Works_{text}")


def _first_line(value: Any) -> str:
    return str(value or "").split("\n")[0].strip()


def display_name(record: dict[str, Any]) -> str:
    data = record.get("data") or {}
    return (
        _first_line(data.get("Name_JP"))
        or _first_line(record.get("display_name"))
        or _first_line(record.get("id"))
        or "(名称未設定)"
    )


def identity_label(record: dict[str, Any]) -> str:
    """作品内でレコードを指す短い識別子（NumberTales は番号、豹変系女子は方位）。"""
    index = ((record.get("record_identity") or {}).get("index")) or {}
    for value in index.values():
        if value not in (None, ""):
            return str(value)
    return str(record.get("id") or "unknown")


def character_slug(record: dict[str, Any]) -> str:
    work = str(record.get("work_key") or "").replace("#Works_", "") or "Work"
    return f"{work}-{identity_label(record)}"


@functools.lru_cache(maxsize=64)
def _db_json(rel: str) -> Any:
    """CreationsDB の ``data/`` 配下の JSON を読む。読めなければ None。"""
    try:
        return json.loads((creations_db_root() / "data" / rel).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def record_badge(record: dict[str, Any]) -> str:
    """インデックスバッジの本体（"57" / "2B" / "EZ1" / "ENE"）。宣言を読めなければ空文字。

    CreationsDB の ``lib/graph/graph-badge.js`` と同じく、作品別 ``db_type.json`` の
    ``$IndexDef.$badge`` の宣言から組み立てる（番号や獣名から機械的に作らない）。
    """
    work_dir = str(record.get("work_key") or "").lstrip("#")
    data = record.get("data") or {}
    types = _db_json(f"{work_dir}/DataBases/db_type.json") or {}
    index_def = (types.get("$IndexDef_" + str(record.get("db_key") or "").removeprefix("#DB_"))
                 or types.get("$IndexDef") or {})
    root, subs = index_def.get("hashTag"), index_def.get("$type")
    pairs = (data.get(root) if isinstance(subs, list) else {root: data.get(root)}) or {}
    spec = index_def.get("$badge") or {}
    keys = spec.get("keys") or [subs[0].get("hashTag") if isinstance(subs, list) and subs else root]

    def value_of(key: Any) -> Any:      # インデックスの値が先、無ければレコードの項目（Num_Badge など）
        return next((source[key] for source in (pairs, data)
                     if isinstance(source, dict) and source.get(key) not in (None, "")), None)

    parts: list[str] = []
    for key in keys:
        key = {"key": key} if isinstance(key, str) else (key or {})
        if key.get("whenMissing") and value_of(key["whenMissing"]) is not None:
            continue
        piece = value_of(key.get("key"))
        if piece is None:
            continue
        piece = str(piece).strip()
        if key.get("codeFrom"):
            # shortcut: 作品別 dict_<キー名>.json の文字列の列だけを引く。$dict の宣言や配列の列は、必要になったら対応する。
            rows = _db_json(f"{work_dir}/Dictionaries/dict_{key['key']}.json") or []
            piece = next((str(row[key["codeFrom"]]).strip() for row in rows
                          if isinstance(row, dict) and str(row.get(key["key"])) == piece
                          and row.get(key["codeFrom"])), piece)
        parts.append(f"{key.get('prefix', '')}{piece}")
    return str(spec.get("separator", "-")).join(parts)


def full_badge(record: dict[str, Any]) -> str:
    """作品コード付きのバッジ（"NTS-57" / "SAR-EZ1"）。公式画像のファイル名に載る表記と同じ。"""
    work = ((_db_json("db_meta.json") or {}).get("CreationWorks") or {}).get(record.get("work_key")) or {}
    return "-".join(part for part in (str(work.get("Works_Code") or ""), record_badge(record)) if part)


def _split_work_code(badge: str) -> tuple[str, str | None]:
    """"SAR-EZ1" → ("EZ1", "#Works_ShauErRiders")。作品コードが付いていなければ (badge, None)。"""
    code, _, body = badge.strip().partition("-")
    works = (_db_json("db_meta.json") or {}).get("CreationWorks") or {}
    work = next((key for key, entry in works.items() if isinstance(entry, dict)
                 and str(entry.get("Works_Code") or "").lower() == code.lower()), None) if code and body else None
    return (body, work) if work else (badge, None)


def _badge_keys(record: dict[str, Any], *, index_only: bool = False) -> set[str]:
    data = record.get("data") or {}
    index = ((record.get("record_identity") or {}).get("index")) or {}
    values = list(index.values()) if index_only else [data.get("Num"), data.get("Num_Badge"), record_badge(record)]
    return {str(v).strip().lower() for v in values if v not in (None, "")}


def _name_fields(record: dict[str, Any]) -> list[str]:
    data = record.get("data") or {}
    values = [
        data.get("Name_JP"), data.get("Name_EN"), data.get("FormalName_JP"),
        data.get("FormalName_EN"), record.get("display_name"), record.get("id"),
    ]
    return [str(v) for v in values if v]


def _describe(record: dict[str, Any]) -> str:
    return f"{record.get('work_key')} / {identity_label(record)} / {display_name(record)}"


def find_character(
    *,
    badge: str | int | None = None,
    name: str | None = None,
    work_key: str | None = None,
) -> dict[str, Any]:
    """番号・バッジ（``badge``）または名前の一部（``name``）でキャラクターを 1 件特定する。

    AI 利用が許可されたレコードだけを候補にする。該当が拒否設定のレコードだけの場合は、
    その理由を添えて :class:`CharacterLookupError` を送出する（生成へ進ませない）。
    """
    if (badge is None) == (name is None):
        raise CharacterLookupError("--badge か --name のどちらか一方を指定してください。")
    work = normalize_work_key(work_key)
    if badge is not None:       # 作品コード付きのバッジ（SAR-EZ1）は、コードから作品を決める
        badge, coded_work = _split_work_code(str(badge))
        work = coded_work or work

    def matches(record: dict[str, Any]) -> bool:
        if work and record.get("work_key") != work:
            return False
        if badge is not None:
            return str(badge).strip().lower() in _badge_keys(record)
        needle = str(name).strip().lower()
        return any(needle in field.lower() for field in _name_fields(record))

    hits = [r for r in load_manifest() if is_character(r) and matches(r)]
    if badge is not None and not hits:
        # バッジが無ければ作品内の識別子で探す。識別子は DB をまたいで重なる
        # （豹変系女子の #DB_Primary "W" と #DB_Regioministrators "W" = バッジ "AW"）ので、バッジを先に見る
        needle = str(badge).strip().lower()
        hits = [r for r in load_manifest() if is_character(r) and (not work or r.get("work_key") == work)
                and needle in _badge_keys(r, index_only=True)]
    allowed = [r for r in hits if generation_gate(r)["allowed"]]
    query = f"badge={badge}" if badge is not None else f"name={name}"
    scope = f"（{work}）" if work else ""
    if len(allowed) == 1:
        return allowed[0]
    if len(allowed) > 1:
        listing = "\n".join(f"  - {_describe(r)}" for r in allowed[:12])
        raise CharacterLookupError(
            f"{query}{scope} に複数のキャラクターが該当します。--work や --badge で絞ってください。\n{listing}"
        )
    if hits:
        reasons = sorted({generation_gate(r)["reason"] for r in hits})
        raise CharacterLookupError(
            f"{query}{scope} に該当するレコードは AI 利用が許可されていません（生成入力から除外）。\n"
            + "\n".join(f"  - {reason}" for reason in reasons)
        )
    raise CharacterLookupError(f"{query}{scope} に該当するキャラクターが manifest にありません。")


def find_any(token: str, work_key: str | None = None) -> dict[str, Any]:
    """番号・バッジとして探し、見つからなければ名前の一部として探す（合同生成・自然文の入口用）。"""
    try:
        return find_character(badge=token, work_key=work_key)
    except CharacterLookupError as err:
        if "manifest にありません" not in str(err):
            raise                       # 複数該当・利用不可は、名前で探し直さずそのまま伝える
    return find_character(name=token, work_key=work_key)


# ── プロンプト向けの要約 ──────────────────────────────────────────────


def _tail(token: Any) -> str:
    """"#BodyPart_Arm" → "Arm"、"#Lat_Right" → "Right"。"""
    text = str(token or "")
    return text.rsplit("_", 1)[-1] if text.startswith("#") else text


def _strings(value: Any) -> list[str]:
    return [str(v) for v in value if v and not str(v).lstrip().upper().startswith("TODO:")] if isinstance(value, list) else []


def resolve_form(record: dict[str, Any], requested: str | None) -> str | None:
    """形態を決める。形態データを持たない作品では None。"""
    forms = ((record.get("ai_hints") or {}).get("forms")) or {}
    if requested:
        if requested not in forms:
            available = ", ".join(forms) or "なし"
            raise CharacterLookupError(
                f"{display_name(record)} に形態 '{requested}' のデータがありません（利用可能: {available}）。"
            )
        return requested
    for form in FORMS:
        if form in forms:
            return form
    return None


def resolve_height_cm(record: dict[str, Any]) -> tuple[float | None, str]:
    """``Height_cm`` を (値, 補足) で返す。複数値は先頭の数値を採り、補足に条件を残す。"""
    raw = (record.get("data") or {}).get("Height_cm")
    if isinstance(raw, (int, float)):
        return float(raw), ""
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict) and isinstance(entry.get("value"), (int, float)):
                return float(entry["value"]), str(entry.get("about_JP") or "")
    return None, ""


# FORMS 以外の Formation は作品内の状態（豹変系女子の豹変前後など）。既定で描く状態だけを通常の項目にする。
# shortcut: 既定の状態は豹変系女子の beforeChange だけ。他作品が状態別の外見を持ったらここへ足す。
_DEFAULT_STATES = ("beforeChange",)


def _drawn_by_default(state: Any, form: str | None) -> bool:
    """その項目を今回の絵に描くか（共通・指定した形態・既定の状態）。"""
    return state in (None, form, *_DEFAULT_STATES)


def _attr_text(attr: dict[str, Any]) -> str:
    """属性 1 件を文字にする。辞書コードは末尾の語、本数は "count: 1"、個体差の補足は括弧で添える。"""
    value = attr.get("value_EN") or attr.get("value_JP")
    if not value:
        value = next((_tail(v) for k, v in attr.items() if k.startswith("vdict_") and v), "")
    if not value and attr.get("value_Num") is not None:
        value = f"{_tail(attr.get('AttrLabel')).lower()}: {attr['value_Num']}"
    about = attr.get("about_EN") or attr.get("about_JP")
    return f"{value} ({about})" if value and about else str(value or about or "")


def _appearance_lines(data: dict[str, Any], form: str | None) -> list[str]:
    lines: list[str] = []
    for entry in data.get("AppearanceDetail") or []:
        if not isinstance(entry, dict):
            continue
        state = entry.get("Formation")
        if state in FORMS and state != form:
            continue
        attrs = [text for attr in entry.get("Attrs") or [] if isinstance(attr, dict) and (text := _attr_text(attr))]
        if not attrs:
            continue
        where = ", ".join(_tail(part) for part in entry.get("BodyPart") or [])
        side = _tail(entry.get("Laterality"))
        if side:
            side = f"character's {side}"
        head = " / ".join(part for part in (where, side, _tail(entry.get("DesignElement"))) if part)
        note = entry.get("Note_EN") or entry.get("Note_JP")
        line = f"{head}: {'; '.join(attrs)}" + (f" ({note})" if note else "")
        if not _drawn_by_default(state, form):
            # 設定画には別状態も描かれている。捨てると観察・検査が通常の姿の特徴と取り違える。
            line = f"[別状態 {state} だけの特徴。今回の絵には描かない] {line}"
        lines.append(line)
    return lines


def _appearance_tail_count(data: dict[str, Any], form: str | None) -> int:
    """尾の項目に書かれた本数の合計。TailsUnit は NumberTales だけが持ち、他作品は本数をここに書く。"""
    return sum(
        attr["value_Num"]
        for entry in data.get("AppearanceDetail") or []
        if isinstance(entry, dict) and _drawn_by_default(entry.get("Formation"), form)
        and "#BodyPart_Tail" in (entry.get("BodyPart") or [])
        for attr in entry.get("Attrs") or []
        if isinstance(attr, dict) and attr.get("AttrLabel") == "#DesignAttr_Count"
        and isinstance(attr.get("value_Num"), int)
    )


def _tail_lines(data: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for unit in data.get("TailsUnit") or []:
        if not isinstance(unit, dict):
            continue
        shape = _tail(unit.get("TailShapeType")) or "Tail"
        count = unit.get("Count")
        branches = [
            f"{_tail(b.get('Laterality'))}: {b.get('ClusterCount')} cluster(s), {b.get('TailCount')} tails"
            for b in unit.get("Branches") or []
            if isinstance(b, dict)
        ]
        text = f"{shape}: {count} tails in total" if count is not None else shape
        lines.append(text + (f" ({'; '.join(branches)})" if branches else ""))
    return lines


def _palette_lines(data: dict[str, Any], form: str | None) -> list[str]:
    lines: list[str] = []
    for color in data.get("ColorPalette") or []:
        if not isinstance(color, dict) or not color.get("Hex"):
            continue
        if color.get("Formation") not in (None, form):
            continue
        parts = ", ".join(_tail(part).lower() for part in color.get("AppliesTo") or [])
        label = color.get("ColorName_JP") or _tail(color.get("Role"))
        note = color.get("Note_EN") or color.get("Note_JP")
        lines.append(f"{label} {color['Hex']}" + (f": {parts}" if parts else "")
                     + (f" ({note})" if note else ""))
    return lines


def summarize_character(record: dict[str, Any], form: str | None = None) -> dict[str, Any]:
    """プロンプトと run_meta に使う要約を作る。manifest に書かれた内容だけを載せる。"""
    data = record.get("data") or {}
    hints = record.get("ai_hints") or {}
    common = hints.get("common") or {}
    resolved_form = resolve_form(record, form)
    form_hints = (hints.get("forms") or {}).get(resolved_form) or {}
    notes = form_hints.get("silhouette_notes") or {}
    height_cm, height_note = resolve_height_cm(record)
    # 構造再同期後も手書きの旧説明は残るため、再同期されたタグを優先する。
    form_tags = _strings(form_hints.get("ai_tags"))
    negative_keywords = _strings(form_hints.get("negative_keywords")) + _strings(form_hints.get("negative_visuals"))
    if form_tags:
        positive_keys = {tag.strip().casefold() for tag in form_tags}
        negative_keywords = [tag for tag in negative_keywords if tag.strip().casefold() not in positive_keys]
    return {
        "name_ja": display_name(record),
        "name_en": _first_line(data.get("Name_EN")),
        "work_key": record.get("work_key"),
        "work_title_ja": _first_line(record.get("work_title_ja")),
        "db_key": record.get("db_key"),
        "identity": identity_label(record),
        "slug": character_slug(record),
        "form": resolved_form,
        "height_cm": height_cm,
        "height_note": height_note,
        "race": data.get("RaceType"),
        "identity_tags": _strings(common.get("identity_tags")),
        "silhouette_features": _strings(common.get("silhouette_features")),
        "form_features": form_tags or (
            _strings(notes.get("body_description")) + _strings(form_hints.get("outfit_features"))
        ),
        "attached_items": [] if form_tags else _strings(notes.get("attached_items")),
        "immutable_constraints": _strings(common.get("immutable_traits"))
        + _strings(form_hints.get("immutable_constraints")),
        "negative_keywords": negative_keywords,
        "appearance_details": _appearance_lines(data, resolved_form),
        "tails": _tail_lines(data),
        "tail_count": sum(unit["Count"] for unit in data.get("TailsUnit") or []
                          if isinstance(unit, dict) and isinstance(unit.get("Count"), int))
        or _appearance_tail_count(data, resolved_form) or None,
        "palette": _palette_lines(data, resolved_form),
        "has_text_design": bool(hints) or bool(data.get("AppearanceDetail")),
    }


# ── 参照画像 ──────────────────────────────────────────────────────────

# CreationsDB の画像フォルダー名 → 参照画像の役割ラベル。該当なしは公式イラスト。
_REFERENCE_ROLES = {
    "concept": "公式設定画",
    "tailsUnit": "公式部位図（尾の構造）",
    "catalog": "公式キャラクターデザイン表",
}
_DEFAULT_REFERENCE_ROLE = "公式イラスト（変形前の姿）"


def _reference_role(rel: str) -> str:
    folders = rel.replace("\\", "/").split("/")[:-1]
    return next((role for name, role in _REFERENCE_ROLES.items() if name in folders), _DEFAULT_REFERENCE_ROLE)


def collect_reference_images(
    record: dict[str, Any], form: str | None, max_images: int = 4
) -> list[dict[str, Any]]:
    """対象キャラクター本人の公式画像を ``[{"path", "rel", "role"}]`` で返す。

    上流が選んだ ``preferred_reference_images`` と尾の部位図だけを使う。別キャラクターの
    差分（concept_variants）や寄稿イラストは選ばない。CreationsDB の ``data/`` 配下に
    実在するファイルに限る。AI 利用が許可されていないレコードには空を返す。
    """
    if not generation_gate(record)["allowed"]:
        return []
    preferred = record.get("preferred_reference_images") or {}
    common = preferred.get("common") or {}
    form_refs = (preferred.get("forms") or {}).get(form) or {} if form else {}
    candidates: list[Any] = [form_refs.get(form), form_refs.get("main"), common.get("concept") or common.get("main")]
    if form != "corefolder":  # コアフォルダ形態は尾の見え方が部位図と異なる
        candidates += (record.get("images") or {}).get("tails_unit") or []
    candidates.append(common.get("catalog"))

    data_root = (creations_db_root() / "data").resolve()
    seen: set[Path] = set()
    refs: list[dict[str, Any]] = []
    for rel in candidates:
        if not isinstance(rel, str) or not rel:
            continue
        path = (creations_db_root() / rel).resolve()
        if path in seen or data_root not in path.parents or not path.is_file():
            continue
        seen.add(path)
        refs.append({"path": path, "rel": rel.replace("\\", "/"), "role": _reference_role(rel), "official": True})
        if len(refs) >= max_images:
            break
    return refs
