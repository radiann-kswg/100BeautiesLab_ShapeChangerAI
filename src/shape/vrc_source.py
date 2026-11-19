"""shape/vrc_source.py — VRCShader_ShapeChange（公開用のシェーダーパッケージ）から形状寸法を読む。

読むのはテキスト 2 ファイルだけ（どちらもラジアンの自作コード）:

- ``ShapeChangeProperties.lilblock`` … マテリアルプロパティの既定値
- ``ShapeChange.hlsl`` … 各モードの完成寸法（``SCHalfSize()`` ほか）

寸法は「元の基準高を 1 とした比」。数値をこのリポジトリへ写さず実行時に読むので、
サブモジュールを更新すれば新しい寸法に追従する。シェーダーの書き方が変わって
読めなくなった場合は、黙って古い値を使わず :class:`ShapeSourceError` で止める。

サブモジュールは公開用のパッケージで、モデルやテクスチャを含まない。読むのはこの 2 ファイルだけ。
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..utils.paths import git_commit, project_root

SHADER_DIR = Path(".")  # 公開用のパッケージでは、2 ファイルともルートにある
HLSL_NAME = "ShapeChange.hlsl"
PROPERTIES_NAME = "ShapeChangeProperties.lilblock"
MODE_COUNT = 16

Vector3 = tuple[float, float, float]


class ShapeSourceError(RuntimeError):
    """シェーダーから必要な値を読めなかった。"""


@dataclass(frozen=True)
class ShaderSource:
    properties: dict[str, Any]
    #: モード 0〜12 の半寸法（基準高比、``_SCSize`` = 1・既定の厚み）。
    half_sizes: dict[int, Vector3]
    constants: dict[str, float]
    commit: str | None
    files: list[dict[str, str]] = field(default_factory=list)

    def prop(self, name: str) -> Any:
        try:
            return self.properties[name]
        except KeyError as err:
            raise ShapeSourceError(f"{PROPERTIES_NAME} にプロパティ {name} がありません。") from err


def shapechange_root() -> Path:
    override = os.environ.get("SHAPECHANGE_REPO_PATH")
    return Path(override) if override else project_root() / "VRCShader_ShapeChange"


# ── プロパティ既定値 ──────────────────────────────────────────────────

_PROPERTY_RE = re.compile(r'(_SC\w+)\s*\("[^"]*"\s*,\s*.+?\)\s*=\s*(.+?)\s*$')


def parse_properties(text: str) -> dict[str, Any]:
    """``_SCName ("label", Type) = default`` の既定値を読む。数値とベクトルのみ。"""
    values: dict[str, Any] = {}
    for line in text.splitlines():
        match = _PROPERTY_RE.search(line)
        if not match:
            continue
        name, raw = match.groups()
        try:
            if raw.startswith("("):
                values[name] = tuple(float(part) for part in raw.strip("()").split(","))
            else:
                values[name] = float(raw)
        except ValueError:
            continue  # テクスチャなど数値でない既定値
    return values


# ── SCHalfSize() ──────────────────────────────────────────────────────


def _component(expr: str, thickness: float) -> float:
    """``0.35`` / ``thickness*0.5`` のような積だけの式を評価する。"""
    value = 1.0
    for factor in expr.split("*"):
        factor = factor.strip()
        value *= thickness if factor == "thickness" else float(factor)
    return value


def _vector(expr: str, thickness: float) -> Vector3:
    expr = expr.strip()
    match = re.fullmatch(r"float3\((.+)\)", expr)
    try:
        if not match:
            scalar = _component(expr, thickness)
            return (scalar, scalar, scalar)
        parts = [_component(part, thickness) for part in match.group(1).split(",")]
    except ValueError as err:
        raise ShapeSourceError(f"SCHalfSize() の式を解釈できません: {expr}") from err
    if len(parts) != 3:
        raise ShapeSourceError(f"SCHalfSize() の式が 3 成分ではありません: {expr}")
    return (parts[0], parts[1], parts[2])


def parse_half_sizes(hlsl: str, thickness: float) -> dict[int, Vector3]:
    """``SCHalfSize()`` から、モード別の半寸法（``_SCSize`` を掛ける前）を読む。"""
    body_match = re.search(r"float3\s+SCHalfSize\s*\(\s*\)\s*\{(.*?)\n\}", hlsl, re.S)
    if not body_match:
        raise ShapeSourceError(f"{HLSL_NAME} に SCHalfSize() が見つかりません。")
    body = body_match.group(1)
    default = re.search(r"float3\s+halfSize\s*=\s*(.+?)\*size\s*;", body)
    clamp = re.search(r"clamp\(_SCMode,\s*0\.0,\s*([\d.]+)\)", body)
    if not default or not clamp:
        raise ShapeSourceError("SCHalfSize() の既定寸法またはモード範囲を読めません。")
    last_mode = int(float(clamp.group(1)))
    sizes = {mode: _vector(default.group(1), thickness) for mode in range(last_mode + 1)}
    for condition, expr in re.findall(r"if\(([^)]*)\)\s*halfSize\s*=\s*(.+?)\*size\s*;", body):
        for mode in re.findall(r"mode\s*==\s*(\d+)", condition):
            sizes[int(mode)] = _vector(expr, thickness)
    return sizes


# ── SCHalfSize() 以外の定数 ───────────────────────────────────────────

# (名前, 正規表現, 説明)。いずれも 1 か所だけ一致することを前提にする。
_CONSTANT_SPECS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("flat_narrow_width", "flat_wide_width"),
     r"_SCMode==11\?([\d.]+):_SCMode==12\?([\d.]+):1\)", "SCFlatScale() の幅倍率（形状 11 / 12）"),
    (("cloth_height",), r"_SCMode==15\?([\d.]+):1\)\*max\(_SCSize", "SCPanelLayout() の反物の高さ比"),
    (("head_span_divisor",),
     r"if\(_SCMode<=5\)shape\.headScale=halfSize\.y\*2\.0/\(headHeight\*([\d.]+)\)",
     "SCGetShape() の頭の基準倍率（形状 0〜5）"),
    (("headed_body_fraction",), r"uprightHeaded\?([\d.]+):1\.0", "SCGetShape() の頭付き形状の胴体比"),
    (("press_half_width", "press_half_depth_per_head_y"),
     r"float2\(([\d.]+),headY\*([\d.]+)\),roundness\)", "SCAddedBasePosition() の縦圧縮の接地寸法"),
)


def parse_constants(hlsl: str) -> dict[str, float]:
    constants: dict[str, float] = {}
    for names, pattern, description in _CONSTANT_SPECS:
        matches = re.findall(pattern, hlsl)
        if len(matches) != 1:
            raise ShapeSourceError(
                f"{HLSL_NAME} から「{description}」を読めません（一致 {len(matches)} 件）。"
                " シェーダーの記述が変わった場合は src/shape/vrc_source.py の正規表現を更新してください。"
            )
        groups = matches[0] if isinstance(matches[0], tuple) else (matches[0],)
        constants.update({name: float(value) for name, value in zip(names, groups)})
    return constants


# ── 読み込み ──────────────────────────────────────────────────────────


def _read(path: Path) -> tuple[str, str]:
    if not path.is_file():
        raise ShapeSourceError(
            f"形状変化シェーダーが見つかりません: {path}\n"
            "  git submodule update --init --recursive を実行してください。"
        )
    raw = path.read_bytes()
    return raw.decode("utf-8").replace("\r\n", "\n"), hashlib.sha256(raw).hexdigest()


def load_shader_source() -> ShaderSource:
    repo = shapechange_root()
    files: list[dict[str, str]] = []
    texts: dict[str, str] = {}
    for name in (PROPERTIES_NAME, HLSL_NAME):
        rel = SHADER_DIR / name
        texts[name], digest = _read(repo / rel)
        files.append({"path": rel.as_posix(), "sha256": digest})
    properties = parse_properties(texts[PROPERTIES_NAME])
    thickness = properties.get("_SCThickness")
    if not isinstance(thickness, float):
        raise ShapeSourceError(f"{PROPERTIES_NAME} から _SCThickness の既定値を読めません。")
    half_sizes = parse_half_sizes(texts[HLSL_NAME], thickness)
    return ShaderSource(
        properties=properties,
        half_sizes=half_sizes,
        constants=parse_constants(texts[HLSL_NAME]),
        commit=git_commit(repo),
        files=files,
    )
