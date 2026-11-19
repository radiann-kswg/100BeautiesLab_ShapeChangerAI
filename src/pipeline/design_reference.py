"""pipeline/design_reference.py — Stage 2: 公式設定画の観察。

対象キャラクター本人の公式画像を Vision に読ませ、見えている特徴を言葉にする。
manifest の文字資料（AIHints・AppearanceDetail）が正典で、観察は補助。観察に失敗したら
利用者に続行／中止を確認し、明示的な続行だけを通す（無回答は中止）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..utils.provider import ProviderError

Vision = Callable[[str, list[tuple[str, Path]]], dict[str, Any]]
Confirm = Callable[[dict[str, Any]], "bool | None"]

_MAX_IMAGES = 3
_KEYS = ("observations", "unknowns", "conflicts")


class ReferenceCancelled(RuntimeError):
    """画像観察なしの続行が承認されなかった。"""


def confirm_in_terminal(warning: dict[str, Any]) -> bool | None:
    """対話端末で続行を尋ねる。端末が無ければ None（＝承認なし）。"""
    print(f"[WARN] {warning['reason']}")
    try:
        return input(f"{warning['question']} [y/N]: ").strip().lower() == "y"
    except EOFError:
        return None


def _canon(character: dict[str, Any]) -> dict[str, Any]:
    keys = ("name_ja", "form", "identity_tags", "silhouette_features", "form_features",
            "appearance_details", "tails", "palette", "immutable_constraints")
    return {key: character[key] for key in keys if character.get(key)}


def observe_design(
    character: dict[str, Any],
    references: list[dict[str, Any]],
    stage_dir: Path,
    *,
    vision: Vision,
    confirm: Confirm | None = None,
) -> dict[str, Any]:
    """観察結果 ``{"observations", "unknowns", "conflicts", "sources", "fallback"}`` を返す。

    結果と失敗理由・回答は ``stage_dir/observation.json`` に残す。
    """
    stage_dir.mkdir(parents=True, exist_ok=True)
    log_path = stage_dir / "observation.json"
    official = [ref for ref in references if ref.get("official")][:_MAX_IMAGES]
    form = f"（{character['form']} form）" if character.get("form") else ""
    prompt = (
        f"公式設定画像から {character['name_ja']}{form} だけを観察してください。"
        "他のキャラクターや別形態の特徴は含めないでください。\n"
        "耳、尾の本数と束の構成、髪型、瞳、衣装の各パーツ、配色、番号や紋章の位置と字形、"
        "左右で異なる要素を観察してください。左右はキャラクター自身の左右で書き、"
        "隠れて見えない部位は推測せず unknowns に入れてください。\n"
        "下の設定資料が正典です。設定資料と食い違う観察は conflicts だけに入れてください。\n"
        "JSON の observations / unknowns / conflicts に、日本語の文字列配列で返してください。\n"
        "[設定資料]\n" + json.dumps(_canon(character), ensure_ascii=False)
    )
    design: dict[str, Any] = {key: [] for key in _KEYS} | {"sources": [], "fallback": False}
    try:
        if not official:
            raise ProviderError("観察できる公式設定画像がありません。")
        result = vision(prompt, [(f"公式画像: {ref['role']}", ref["path"]) for ref in official])
        for key in _KEYS:
            values = result.get(key)
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                raise ProviderError(f"画像観察の {key} が文字列の配列ではありません。")
            design[key] = values
        if not design["observations"]:
            raise ProviderError("公式画像から特徴を読み取れませんでした。")
    except ProviderError as err:
        warning = {
            "character": character["name_ja"],
            "reason": f"公式設定画の観察に失敗しました: {err}",
            "question": "画像観察なしで、設定資料と参照画像だけで生成を続けますか？",
        }
        log_path.write_text(json.dumps({"status": "awaiting_confirmation", "warning": warning},
                                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        answer = (confirm or confirm_in_terminal)(warning)
        if answer is not True:
            decision = "cancel" if answer is False else "not_received"
            log_path.write_text(json.dumps({"status": "cancelled", "warning": warning, "decision": decision},
                                           ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            raise ReferenceCancelled(
                "画像観察なしの続行が承認されなかったため、ラフ生成の前に中止しました。"
            ) from err
        design["fallback"] = True
        log_path.write_text(json.dumps({"status": "fallback_approved", "warning": warning, "design": design},
                                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return design
    design["sources"] = [ref["rel"] for ref in official]
    log_path.write_text(json.dumps({"status": "ok", "prompt": prompt, "design": design},
                                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return design


def observation_block(design: dict[str, Any]) -> str:
    """プロンプトと検査に添える観察ブロック。観察が無ければ空文字。"""
    if not design.get("observations"):
        return ""
    lines = ["[公式設定画の観察（補助）]",
             "設定資料が正典。下の観察は補助で、設定資料と食い違う場合は設定資料を採る。"]
    lines += [f"- {item}" for item in design["observations"]]
    if design.get("unknowns"):
        lines.append("- 画像から分からなかった点（確定した特徴として描かない）: " + " / ".join(design["unknowns"]))
    return "\n".join(lines)
