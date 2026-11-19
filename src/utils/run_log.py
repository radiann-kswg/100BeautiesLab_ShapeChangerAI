"""utils/run_log.py — 実行ごとの記録（prompt.txt / run_meta.json / notes.md）。

AGENTS.md「出力と記録」の 3 ファイルを、失敗時も含めて必ず残す。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

_NOTES_TEMPLATE = """# 確認メモ

- 実行フォルダー: {run_name}
- 対象: {character}
- 形状: {shape}（{layout}）
- サービス / モデル: {provider} / {model}
{retry_line}
## 確認結果

元設定と見比べて、該当する項目にチェックと所見を書く。

- [ ] 輪郭が指定の形状になっている:
- [ ] 縦横比と厚みが指定に近い:
- [ ] 顔の位置と大きさ:
- [ ] 手足・衣装・付属物の見え方:
- [ ] キャラクターを見分ける特徴（髪色・瞳色・耳・尻尾の本数・番号など）:

## 再試行で見直す点

-
"""


def write_run_meta(run_dir: Path, meta: dict[str, Any]) -> Path:
    """run_meta.json へ追記マージする（既存キーは新しい値で更新）。"""
    path = Path(run_dir) / "run_meta.json"
    existing: dict[str, Any] = {}
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    merged = {**existing, **meta}
    merged.setdefault("created_at", now)
    merged["updated_at"] = now
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def initialize_run_logs(
    run_dir: Path,
    *,
    prompt_text: str,
    meta: dict[str, Any],
    character: str,
    shape: str,
    layout: str,
) -> None:
    """生成 API を呼ぶ前に 3 ファイルを作る。API が失敗してもプロンプトと条件が残る。"""
    run_dir = Path(run_dir)
    (run_dir / "prompt.txt").write_text(prompt_text, encoding="utf-8")
    write_run_meta(run_dir, {**meta, "status": "running", "results": [], "errors": []})
    retry_of = meta.get("retry_of")
    (run_dir / "notes.md").write_text(
        _NOTES_TEMPLATE.format(
            run_name=run_dir.name,
            character=character,
            shape=shape,
            layout=layout,
            provider=meta.get("service", ""),
            model=meta.get("model", ""),
            retry_line=(
                f"- 再生成元: {retry_of}\n- 今回変えた点: {'; '.join(meta.get('revisions') or []) or '(未記入)'}\n"
                if retry_of
                else ""
            ),
        ),
        encoding="utf-8",
    )


def finalize_run_logs(
    run_dir: Path, *, status: str, results: list[Any], errors: list[str], extra: dict[str, Any] | None = None
) -> Path:
    return write_run_meta(run_dir, {**(extra or {}), "status": status, "results": results, "errors": errors})
