"""形状寸法の読み取り（VRCShader_ShapeChange → src/shape）の回帰テスト。

    python tests/test_shape_source.py     # 自己完結ランナー
    python -m pytest tests/               # pytest を入れている場合

合成したシェーダー断片で読み取りの規則を、実サブモジュールで全 16 形状が
組み立てられることを確認する。寸法の数値そのものはシェーダー側の調整で変わるため、
実サブモジュールに対しては値を固定せず、形状の性質（立方体は等辺など）だけを見る。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.shape.modes import build_shape_spec  # noqa: E402
from src.shape.vrc_source import (  # noqa: E402
    MODE_COUNT,
    ShapeSourceError,
    load_shader_source,
    parse_constants,
    parse_half_sizes,
    parse_properties,
)

_HLSL = """
float3 SCHalfSize()
{
    int mode=(int)round(clamp(_SCMode,0.0,12.0));
    float thickness=max(_SCThickness,0.002),size=max(_SCSize,0.05);
    float3 halfSize=float3(0.35,0.35,0.35)*size;
    if(mode==1) halfSize=float3(0.24,0.11,0.43)*size;
    if(mode==2) halfSize=0.28*size;
    if(mode==6 || mode==7) halfSize=float3(0.28,0.5,0.28)*size;
    if(mode==8) halfSize=float3(0.4,0.4,thickness*0.5)*size;
    if(mode==1)halfSize*=max(_SCBoxAspect.xyz,float3(0.1,0.1,0.1));
    return halfSize;
}
"""


def test_parse_properties_reads_numbers_and_vectors():
    text = (
        '        _SCThickness ("平面・コインの厚み / 身長比", Range(0.002,0.2)) = 0.025\n'
        '        [Enum(Keep,0,Fold,1,Hide,2)] _SCArms ("腕", Float) = 1\n'
        '        [HideInInspector] _SCMeasure ("Neck / head size", Vector) = (0.8,0.2,0.15,0.08)\n'
        '        [HideInInspector] _SCSheetTex ("Front / back", 2D) = "white" {}\n'
    )
    props = parse_properties(text)
    assert props == {"_SCThickness": 0.025, "_SCArms": 1.0, "_SCMeasure": (0.8, 0.2, 0.15, 0.08)}


def test_parse_half_sizes_follows_shader_assignments():
    sizes = parse_half_sizes(_HLSL, thickness=0.025)
    assert sorted(sizes) == list(range(13))
    assert sizes[0] == (0.35, 0.35, 0.35)          # 既定寸法
    assert sizes[1] == (0.24, 0.11, 0.43)          # 倍率の *= 行は寸法として拾わない
    assert sizes[2] == (0.28, 0.28, 0.28)          # スカラーは 3 成分へ展開
    assert sizes[6] == sizes[7] == (0.28, 0.5, 0.28)
    assert sizes[8] == (0.4, 0.4, 0.0125)          # thickness を既定値で評価


def test_unreadable_shader_stops_instead_of_guessing():
    for broken in ("float3 Other(){}", _HLSL.replace("0.28*size", "pow(size,2)*size")):
        try:
            parse_half_sizes(broken, thickness=0.025)
        except ShapeSourceError:
            continue
        raise AssertionError("読めないシェーダーで例外にならなかった")
    try:
        parse_constants(_HLSL)  # 幅倍率などの定数が無い
    except ShapeSourceError as err:
        assert "vrc_source.py" in str(err)
    else:
        raise AssertionError("定数が無いのに例外にならなかった")


def test_real_submodule_builds_all_modes():
    source = load_shader_source()
    assert source.commit and len(source.files) == 2
    specs = [build_shape_spec(mode, source, height_cm=150.0) for mode in range(MODE_COUNT)]
    cube, sphere, coin, cloth, upright = specs[0], specs[4], specs[8], specs[15], specs[10]
    assert cube["dimensions"]["width"] == cube["dimensions"]["height"] == cube["dimensions"]["depth"]
    assert sphere["dimensions"]["width"] == sphere["dimensions"]["height"]
    assert coin["dimensions"]["depth"] < coin["dimensions"]["width"] / 10      # 薄い円盤
    assert cloth["dimensions"]["height"] > 1.0 and cloth["dimensions"]["bend"]  # 身長より長い布
    assert upright["dimensions"]["width"] is None and upright["dimensions"]["width_factor"] == 1.0
    assert specs[11]["dimensions"]["width_factor"] < 1.0 < specs[12]["dimensions"]["width_factor"]
    # cm は比率 × 身長
    assert cube["dimensions_cm"]["height"] == round(cube["dimensions"]["height"] * 150.0, 1)
    assert cube["face"]["span_cm"] == round(cube["face"]["span"] * 150.0, 1)


def test_limb_and_appendage_options_follow_mode():
    source = load_shader_source()
    sphere = build_shape_spec(4, source, arms="keep", appendages="integrate")
    assert sphere["limbs"]["arms"] == "keep" and sphere["limbs"]["legs"] in ("keep", "fold", "hide")
    assert sphere["appendages"] == "integrate" and not sphere["warnings"]
    panel = build_shape_spec(14, source, arms="hide", appendages="keep")
    assert "arms" not in panel["limbs"] and panel["appendages"] == "merged"
    # 形を保つ尾の補足は、補足のある形状で keep のときだけ付く
    from src.shape.modes import appendage_text
    assert "垂らさない" in appendage_text(build_shape_spec(3, source, appendages="keep"))[0]
    assert "垂らさない" not in appendage_text(build_shape_spec(3, source, appendages="integrate"))[0]
    assert appendage_text(build_shape_spec(4, source, appendages="keep")) == appendage_text({"appendages": "keep"})
    assert len(panel["warnings"]) == 2             # 選べない指定は無視して知らせる
    for bad in (dict(mode=16), dict(mode=4, size=3.0)):
        try:
            build_shape_spec(bad.pop("mode"), source, **bad)
        except ShapeSourceError:
            continue
        raise AssertionError("範囲外の指定が通った")


def test_vm_bundle_holds_only_the_two_shader_files():
    import os
    import tempfile

    from src.shape.vm_bundle import write_bundle

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "bundle"
        files = write_bundle(dest)
        found = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())
        assert found == sorted(f["path"] for f in files) and len(found) == 2
        try:
            write_bundle(dest)                      # 空でない出力先には書かない
        except ValueError:
            pass
        else:
            raise AssertionError("空でない出力先を受け付けた")
        expected = load_shader_source().half_sizes
        previous = os.environ.get("SHAPECHANGE_REPO_PATH")
        os.environ["SHAPECHANGE_REPO_PATH"] = str(dest)   # Cloud Run と同じく 2 ファイルだけで読めること
        try:
            assert load_shader_source().half_sizes == expected
        finally:
            if previous is None:
                del os.environ["SHAPECHANGE_REPO_PATH"]
            else:
                os.environ["SHAPECHANGE_REPO_PATH"] = previous


if __name__ == "__main__":
    from _runner import run_module_tests

    run_module_tests(globals())
