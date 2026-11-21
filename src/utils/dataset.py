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
import re
from pathlib import Path
from typing import Any, Iterator

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
        return [_usual_costume_only(json.loads(line), line) for line in handle if line.strip()]


# 描く衣装は 1 着だけ。別衣装の項目が混ざると、2 着分の特徴が 1 枚に出る。指定が無ければ通常衣装で、
# DB に項目のある別衣装は with_costume() で選ぶ。
_DEFAULT_COSTUME = "usual"
# 衣装デザインが第三者のもので、AI 向けの利用に了承が無い衣装。データに現れても選ばせない。
_AI_EXCLUDED_COSTUMES = frozenset({"idol"})
# 権利上 AI に渡さない画像。上流で指定できるようになるまで下流で外す（CreationsDB #62）。
# NumberTales 61 のアイドル衣装（デザインが第三者のもの）を描いた画像で、衣装の記述は
# 上の「通常衣装だけ」で落ちる。衣装を選べるようにするときは、この衣装を選択肢から外す。
_AI_EXCLUDED_IMAGE_STEMS = frozenset({
    "chr-dsgn-alt_wipNTS-61-idol", "emstk_corefolderNTS-61-idol1", "emstk_corefolderNTS-61-idol2",
})


def is_ai_excluded_image(path: Any) -> bool:
    """AI に渡さない画像か。パス・URL・拡張子なしの DB 記載名のどれでも受ける。"""
    return isinstance(path, str) and Path(path.replace("\\", "/")).stem in _AI_EXCLUDED_IMAGE_STEMS


def _drop_excluded_images(node: Any) -> Any:
    if isinstance(node, dict):
        return {key: _drop_excluded_images(value) for key, value in node.items()
                if not is_ai_excluded_image(value)}
    if isinstance(node, list):
        return [_drop_excluded_images(item) for item in node
                if not is_ai_excluded_image(item.get("path") if isinstance(item, dict) else item)]
    return node


def _entry_texts(entry: dict[str, Any]) -> set[str]:
    return {value.strip() for attr in entry.get("Attrs") or [] if isinstance(attr, dict)
            for value in (attr.get("value_EN"), attr.get("value_JP"))
            if isinstance(value, str) and value.strip()}


def _record_key(record: dict[str, Any]) -> tuple[Any, ...]:
    return (record.get("_type"), record.get("work_key"), record.get("db_key"), record.get("record_identity"))


def _raw_record(record: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """衣装で絞る前の、manifest のままのレコードと、その行。"""
    key = _record_key(record)
    with manifest_path().open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip() and _record_key(raw := json.loads(line)) == key:
                return raw, line
    return record, ""


def costume_names(record: dict[str, Any]) -> list[str]:
    """DB に項目があって選べる衣装の名前（通常衣装を含む。AI 向けに使えない衣装は除く）。"""
    details = (_raw_record(record)[0].get("data") or {}).get("AppearanceDetail") or []
    names = {entry["Costume"] for entry in details if isinstance(entry, dict) and entry.get("Costume")}
    return sorted(names - _AI_EXCLUDED_COSTUMES)


def with_costume(record: dict[str, Any], costume: str) -> dict[str, Any]:
    """同じキャラクターを、指定の衣装の項目で読み直す（ほかの衣装の項目と文面は落ちる）。"""
    if costume not in costume_names(record):
        raise CharacterLookupError(
            f"{display_name(record)} に衣装 '{costume}' の設定がありません（選べる衣装: "
            f"{', '.join(costume_names(record)) or 'なし'}）。")
    raw, line = _raw_record(record)
    return _usual_costume_only(raw, line, costume)


def _usual_costume_only(record: dict[str, Any], raw_line: str = "", costume: str = _DEFAULT_COSTUME) -> dict[str, Any]:
    """選んだ衣装（既定は usual）以外の衣装の記述と、AI に渡さない画像を外したレコードを返す。

    AppearanceDetail の項目に加え、AIHints に写されたその項目の文面も落とす。AIHints は項目の
    文面をそのまま、または ", " でつないで持つので、落とす項目だけが持つ文面で照合する。
    """
    if any(stem in raw_line for stem in _AI_EXCLUDED_IMAGE_STEMS):
        record = dict(record)
        for key in ("images", "preferred_reference_images", "ai_hints"):
            if record.get(key):
                record[key] = _drop_excluded_images(record[key])

    data = record.get("data")
    details = data.get("AppearanceDetail") if isinstance(data, dict) else None
    entries = [e for e in details or [] if isinstance(e, dict)]

    def gone(entry: dict[str, Any]) -> bool:
        return bool(entry.get("Costume")) and entry["Costume"] != costume

    if not any(gone(e) for e in entries):
        return record
    kept = set().union(*(_entry_texts(e) for e in entries if not gone(e)))
    texts = sorted(set().union(*(_entry_texts(e) for e in entries if gone(e))) - kept, key=len, reverse=True)
    alternatives = "|".join(re.escape(text) for text in texts)
    listed = re.compile(rf"(?:^|[,:] )(?:{alternatives})(?:$|, |\))") if texts else None

    def scrub(node: Any) -> Any:
        if isinstance(node, dict):
            # つないだ文（prompt_export 等）は、部分的に削ると残骸が出るので空にする。要約には使っていない。
            return {key: "" if isinstance(value, str) and any(text in value for text in texts) else scrub(value)
                    for key, value in node.items()}
        if isinstance(node, list):
            return [scrub(item) for item in node
                    if not (listed and isinstance(item, str) and listed.search(item))]
        return node

    data = dict(data, AppearanceDetail=[e for e in data["AppearanceDetail"]
                                        if not (isinstance(e, dict) and gone(e))])
    if data.get("AIHints"):
        data["AIHints"] = scrub(data["AIHints"])
    record = dict(record, data=data)
    if record.get("ai_hints"):
        record["ai_hints"] = scrub(record["ai_hints"])
    return record


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


def resolve_height_cm(record: dict[str, Any], state: str = "") -> tuple[float | None, str]:
    """``Height_cm`` を (値, 補足) で返す。複数値は、状態の指定に条件の語が出てくる値、無ければ先頭の値を採る。"""
    raw = (record.get("data") or {}).get("Height_cm")
    if isinstance(raw, (int, float)):
        return float(raw), ""
    if isinstance(raw, list):
        entries = [e for e in raw if isinstance(e, dict) and isinstance(e.get("value"), (int, float))]
        wanted = state.casefold()
        named = [e for e in entries if any(
            (word := str(e.get(key) or "").removesuffix("時").strip().casefold()) and word in wanted
            for key in ("about_JP", "about_EN"))]
        for entry in named or entries:
            return float(entry["value"]), str(entry.get("about_JP") or "")
    return None, ""


# FORMS 以外の Formation は作品内の状態（豹変系女子の豹変前後など）。既定で描く状態だけを通常の項目にする。
# 既定で描くのは、豹変系女子の beforeChange（豹変前）と獣爾騎兵の normal（獣化前）。
# shortcut: 既定の状態は作品ごとの語を並べただけ。他作品が状態別の外見を持ったらここへ足す。
_DEFAULT_STATES = ("beforeChange", "normal")


def _drawn_by_default(state: Any, form: str | None, drawn: tuple[str, ...] = _DEFAULT_STATES) -> bool:
    """その項目を今回の絵に描くか（共通・指定した形態・描く状態。描く状態の既定は ``_DEFAULT_STATES``）。"""
    return state in (None, form, *drawn)


def state_names(record: dict[str, Any]) -> list[str]:
    """DB に項目があって名前で選べる状態（豹変系女子の afterChanged、獣爾騎兵の beasted など）。"""
    details = (record.get("data") or {}).get("AppearanceDetail") or []
    return sorted({entry["Formation"] for entry in details
                   if isinstance(entry, dict) and entry.get("Formation") and entry["Formation"] not in FORMS})


def _detachable_lines(data: dict[str, Any], form: str | None, drawn: tuple[str, ...]) -> list[str]:
    """着脱できる印（``Detachable``）の付いた、今回描く着用物。状態の指定で外せるのはこれだけ。"""
    lines: list[str] = []
    for entry in data.get("AppearanceDetail") or []:
        if (isinstance(entry, dict) and entry.get("Detachable")
                and _drawn_by_default(entry.get("Formation"), form, drawn)):
            texts = [_attr_text(attr) for attr in entry.get("Attrs") or [] if isinstance(attr, dict)]
            lines += [text for text in texts if text][:1]
    return lines


def _attr_text(attr: dict[str, Any]) -> str:
    """属性 1 件を文字にする。辞書コードは末尾の語、本数は "count: 1"、個体差の補足は括弧で添える。"""
    value = attr.get("value_EN") or attr.get("value_JP")
    if not value:
        value = next((_tail(v) for k, v in attr.items() if k.startswith("vdict_") and v), "")
    if not value and attr.get("value_Num") is not None:
        value = f"{_tail(attr.get('AttrLabel')).lower()}: {attr['value_Num']}"
    about = attr.get("about_EN") or attr.get("about_JP")
    return f"{value} ({about})" if value and about else str(value or about or "")


def _appearance_lines(data: dict[str, Any], form: str | None,
                      drawn: tuple[str, ...] = _DEFAULT_STATES) -> list[str]:
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
        if not _drawn_by_default(state, form, drawn):
            # 設定画には別状態も描かれている。捨てると観察・検査が通常の姿の特徴と取り違える。
            line = f"[別状態 {state} だけの特徴。今回の絵には描かない] {line}"
        lines.append(line)
    return lines


def _appearance_tail_count(data: dict[str, Any], form: str | None,
                           drawn: tuple[str, ...] = _DEFAULT_STATES) -> int:
    """尾の項目に書かれた本数の合計。TailsUnit は NumberTales だけが持ち、他作品は本数をここに書く。"""
    return sum(
        attr["value_Num"]
        for entry in data.get("AppearanceDetail") or []
        if isinstance(entry, dict) and _drawn_by_default(entry.get("Formation"), form, drawn)
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


def _palette_lines(data: dict[str, Any], form: str | None,
                   drawn: tuple[str, ...] = _DEFAULT_STATES) -> list[str]:
    lines: list[str] = []
    for color in data.get("ColorPalette") or []:
        if not isinstance(color, dict) or not color.get("Hex"):
            continue
        if not _drawn_by_default(color.get("Formation"), form, drawn):
            continue
        parts = ", ".join(_tail(part).lower() for part in color.get("AppliesTo") or [])
        label = color.get("ColorName_JP") or _tail(color.get("Role"))
        note = color.get("Note_EN") or color.get("Note_JP")
        lines.append(f"{label} {color['Hex']}" + (f": {parts}" if parts else "")
                     + (f" ({note})" if note else ""))
    return lines


def summarize_character(record: dict[str, Any], form: str | None = None, *,
                        state: str = "", costume: str = "") -> dict[str, Any]:
    """プロンプトと run_meta に使う要約を作る。manifest に書かれた内容だけを載せる。

    ``state`` と ``costume`` は、DB にある名前（``state_names`` / ``costume_names``）ならその項目を選ぶ。
    名前に当たらない文は、体型と衣装・着用物だけを変える自由な指定として ``*_note`` に残す。
    """
    state, costume = " ".join(state.split()), " ".join(costume.split())
    states, costumes = state_names(record), costume_names(record) if record.get("_type") else []
    named_state = state if state in states else ""
    named_costume = costume if costume in costumes else ""
    if costume in _AI_EXCLUDED_COSTUMES:
        raise CharacterLookupError(f"衣装 '{costume}' は、AI 向けの利用から外しているため選べません。")
    if named_costume and named_costume != _DEFAULT_COSTUME:
        record = with_costume(record, named_costume)
    drawn = (named_state,) if named_state else _DEFAULT_STATES
    data = record.get("data") or {}
    hints = record.get("ai_hints") or {}
    common = hints.get("common") or {}
    resolved_form = resolve_form(record, form)
    form_hints = (hints.get("forms") or {}).get(resolved_form) or {}
    notes = form_hints.get("silhouette_notes") or {}
    height_cm, height_note = resolve_height_cm(record, state)
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
        "appearance_details": _appearance_lines(data, resolved_form, drawn),
        "tails": _tail_lines(data),
        "tail_count": sum(unit["Count"] for unit in data.get("TailsUnit") or []
                          if isinstance(unit, dict) and isinstance(unit.get("Count"), int))
        or _appearance_tail_count(data, resolved_form, drawn) or None,
        "palette": _palette_lines(data, resolved_form, drawn),
        "has_text_design": bool(hints) or bool(data.get("AppearanceDetail")),
        "state": named_state, "state_note": "" if named_state else state, "states": states,
        "costume": named_costume, "costume_note": "" if named_costume else costume, "costumes": costumes,
        "detachable": _detachable_lines(data, resolved_form, drawn),
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


def _image_paths(node: Any) -> Iterator[str]:
    if isinstance(node, str):
        if node.startswith("data/"):
            yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _image_paths(value)
    elif isinstance(node, list):
        for value in node:
            yield from _image_paths(value)


@functools.lru_cache(maxsize=4)
def _group_illustrations(manifest: str, mtime: float) -> frozenset[str]:
    """2 件以上のキャラクターレコードが参照する公式イラスト（複数のキャラクターが 1 枚に写る絵）。

    上流のデータには、イラストに誰が写っているかの情報が無い。設定画や部位図は、同じ人物を別の DB から
    指すことがあるので数えない。
    """
    owners: dict[str, set[str]] = {}
    with open(manifest, encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line) if line.strip() else {}
            if not is_character(record):
                continue
            key = json.dumps(_record_key(record), ensure_ascii=False, sort_keys=True)
            for rel in {*_image_paths(record.get("images")), *_image_paths(record.get("preferred_reference_images"))}:
                if _reference_role(rel) == _DEFAULT_REFERENCE_ROLE:
                    owners.setdefault(rel, set()).add(key)
    return frozenset(rel for rel, keys in owners.items() if len(keys) > 1)


def group_illustrations() -> frozenset[str]:
    target = manifest_path()
    return _group_illustrations(str(target), target.stat().st_mtime)


def collect_reference_images(
    record: dict[str, Any], form: str | None, max_images: int = 4
) -> list[dict[str, Any]]:
    """対象キャラクター本人の公式画像を ``[{"path", "rel", "role"}]`` で返す。

    上流が選んだ ``preferred_reference_images`` と尾の部位図だけを使う。別キャラクターの
    差分（concept_variants）や寄稿イラストは選ばない。CreationsDB の ``data/`` 配下に
    実在するファイルに限る。AI 利用が許可されていないレコードには空を返す。

    ほかのキャラクターも一緒に写るイラストは、本人だけの画像が 1 枚でもあれば使わない（誰がどこに
    写っているかを伝える手段が無く、別のキャラクターの髪や配色が混ざるため）。それしか無いときは、
    役割にその旨を書いて渡す。
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
    shared = group_illustrations()
    seen: set[Path] = set()
    refs: list[dict[str, Any]] = []
    group: list[dict[str, Any]] = []
    for rel in candidates:
        if not isinstance(rel, str) or not rel:
            continue
        path = (creations_db_root() / rel).resolve()
        if path in seen or data_root not in path.parents or not path.is_file():
            continue
        seen.add(path)
        rel = rel.replace("\\", "/")
        if rel in shared:
            group.append({"path": path, "rel": rel, "official": True, "role":
                          f"公式イラスト（ほかのキャラクターも一緒に写る。{display_name(record)} だけを見る）"})
            continue
        refs.append({"path": path, "rel": rel, "role": _reference_role(rel), "official": True})
        if len(refs) >= max_images:
            break
    return refs or group[:max_images]
