"""Cloud Run のイメージへ渡す形状データを書き出す。対象は、パイプラインが読むシェーダーの 2 ファイルだけ。

    python -m src.shape.vm_bundle outputs/vm-shape-data

出力したフォルダーの場所を ``SHAPECHANGE_REPO_PATH`` で指す。deploy/deploy_cloud_run.py が使う。
VM は公開用のシェーダーパッケージをサブモジュールとして取得するので、このコマンドは使わない。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from .vrc_source import HLSL_NAME, PROPERTIES_NAME, SHADER_DIR, load_shader_source, shapechange_root


def write_bundle(dest: Path) -> list[dict[str, str]]:
    """``dest`` に 2 ファイルだけを同じ相対パスで書き出し、パスと SHA-256 を返す。"""
    if dest.exists() and any(dest.iterdir()):
        raise ValueError(f"出力先が空ではありません: {dest}\n  ほかのファイルが混ざらないよう、空のフォルダーを指定してください。")
    source = load_shader_source()  # 読み取れない版は VM に送らない
    for name in (PROPERTIES_NAME, HLSL_NAME):
        target = dest / SHADER_DIR / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(shapechange_root() / SHADER_DIR / name, target)
    return source.files


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("使い方: python -m src.shape.vm_bundle <空の出力フォルダー>", file=sys.stderr)
        return 2
    try:
        files = write_bundle(Path(args[0]))
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    for entry in files:
        print(f"{entry['sha256']}  {entry['path']}")
    print(f"イメージへ入れるのはこのフォルダーの中身だけ: {Path(args[0]).resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
