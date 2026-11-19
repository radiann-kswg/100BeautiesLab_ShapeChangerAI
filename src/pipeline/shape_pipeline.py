"""pipeline/shape_pipeline.py — 形状変化イラスト生成パイプライン（CLI と実行本体）。

入力:
  - キャラクター: ``_creations-ai/ai-dataset/manifest.jsonl``（AI 利用が許可されたレコードのみ）
  - 形状と体格: ``VRCShader_ShapeChange`` のシェーダー（寸法）と ``src/shape/mode_descriptions.json``（記述）

5 段階（GeneratorsAI の画像パイプラインと同じ区切り）:
  Stage 1  キャラクターと形状の仕様からプロンプトを組み立てる（LLM は使わない）
  Stage 2  公式設定画を Vision で観察する（失敗時は続行／中止を確認）
  Stage 3  ラフを生成する
  Stage 4  ラフを検査し、問題のあるものを修正または作り直す
  Stage 5  検査を通ったラフから完成画像を清書し、Canva で仕上げる

``--quick`` は Stage 1 のプロンプトで 1 回だけ生成する（検査なし）。

使い方:
    python -m src.pipeline.shape_pipeline --badge 57 --mode 4 --dry-run
    python -m src.pipeline.shape_pipeline --badge 57 --mode 4
    python -m src.pipeline.shape_pipeline --badge 57 --mode 4 --quick
    python -m src.pipeline.shape_pipeline --joint 85:0,73:3
    python -m src.pipeline.shape_pipeline --natural "85(ハッコ)が箱化している様子"
    python -m src.pipeline.shape_pipeline --badge 57 --mode 4 --iterate-from <実行フォルダー名> --revisions "尾を 7 本に"
    python -m src.pipeline.shape_pipeline --list-modes

課金を伴うため、先に ``--dry-run`` でプロンプトと参照画像を確認してから本番実行する。
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..shape.modes import APPENDAGE_CHOICES, LIMB_CHOICES, build_shape_spec
from ..shape.vrc_source import MODE_COUNT, ShaderSource, ShapeSourceError, load_shader_source, shapechange_root
from ..utils import dataset
from ..utils.paths import build_run_dir, default_output_base, git_commit, project_root, references_dir
from ..utils.provider import GenerationResult, ProviderError
from ..utils.run_log import finalize_run_logs, initialize_run_logs
from .correction_generator import correct_roughs
from .design_reference import Confirm, ReferenceCancelled, Vision, observation_block, observe_design
from .final_generator import JOINT_HEADER, SINGLE_HEADER, generate_finals, rough_reference
from .prompt_builder import ITERATE_ROLE, LAYOUT_ASPECT, LAYOUTS, build_joint_prompt, build_prompt

PROVIDERS = ("gemini", "openai")
Generator = Callable[..., GenerationResult]
#: ステージ完了の通知。(ステージ名, 画像パス, 実行フォルダー) を受け取る。
StageCallback = Callable[[str, list[Path], Path], None]


class PipelineInputError(ValueError):
    """キャラクター・形状・参照などの指定が実行できない内容だった。"""


def _load_env() -> None:
    env_file = project_root() / ".env"
    try:
        from dotenv import load_dotenv
    except ImportError:
        if env_file.is_file():
            print("[WARN] python-dotenv が未インストールのため .env を読み込めません: pip install -r requirements.txt")
        return
    load_dotenv(env_file)


def _provider(name: str) -> tuple[Generator, str]:
    if name == "openai":
        from ..openai import generate as module
    else:
        from ..gemini import generate as module
    return module.generate_images, module.resolve_model()


def resolve_extra_references(paths: list[str], output_base: Path) -> list[dict[str, Any]]:
    """``--ref`` の追加参照を検証する。置き場所は references/ か outputs/ に限る。

    AI へ渡す素材は権利条件を確認して選ぶ（AGENTS.md「著作物の扱い」）。確認済みの素材の
    置き場である references/ と、自分の生成結果である outputs/ 以外は受け付けない。
    VRCShader_ShapeChange 配下（第三者のモデルやその描画を含む）は場所を問わず拒否する。
    """
    refs: list[dict[str, Any]] = []
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"--ref のファイルが見つかりません: {raw}")
        if path.is_relative_to(shapechange_root().resolve()):
            raise ValueError(
                f"--ref に VRCShader_ShapeChange 配下のファイルは使えません: {raw}\n"
                "  第三者のモデル・テクスチャ・描画画像はローカルでの閲覧にとどめます。"
            )
        if path.is_relative_to(output_base.resolve()):
            role = "以前の生成結果（改稿の起点）"
        elif path.is_relative_to(references_dir().resolve()):
            role = "手元の追加資料"
        else:
            raise ValueError(
                f"--ref は references/ か outputs/ の中のファイルを指定してください: {raw}\n"
                "  出典・作者・権利条件を確認した素材を references/ に置いてから指定します。"
            )
        refs.append({"path": path, "rel": str(path), "role": role, "official": False})
    return refs


def _source_meta(source: ShaderSource) -> dict[str, Any]:
    header = next((r for r in dataset.load_manifest() if r.get("_type") == "dataset_header"), {})
    return {
        "creations_ai": {
            "manifest": "_creations-ai/ai-dataset/manifest.jsonl",
            "commit": git_commit(dataset.creations_ai_root()),
            "generated_at": header.get("generated_at"),
            "creations_db_commit": header.get("submodule_commit"),
        },
        "shapechange": {"commit": source.commit, "files": source.files},
    }


def _pad(text: str, width: int) -> str:
    """全角を 2 桁として数えて右側を空白で埋める。"""
    used = sum(2 if unicodedata.east_asian_width(ch) in "WFA" else 1 for ch in text)
    return text + " " * max(width - used, 1)


def _print_modes(source: ShaderSource) -> None:
    print(f"番号  {_pad('形状', 24)}幅 × 高さ × 奥行き（変形前の身長を 1 とした比）")
    for mode in range(MODE_COUNT):
        spec = build_shape_spec(mode, source)
        dims = spec["dimensions"]
        width = f"{dims['width']:g}" if dims["width"] is not None else f"輪郭の {dims['width_factor']:g} 倍"
        print(f"{mode:>4}  {_pad(spec['name_ja'], 24)}{width} × {dims['height']:g} × {dims['depth']:g}")


def _print_characters(work_key: str | None) -> None:
    work = dataset.normalize_work_key(work_key)
    for record in dataset.get_characters():
        if work and record.get("work_key") != work:
            continue
        print(f"{record.get('work_key')}\t{dataset.full_badge(record)}\t{dataset.identity_label(record)}"
              f"\t{dataset.display_name(record)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.pipeline.shape_pipeline",
        description="創作キャラクターの形状変化イラストを生成する。",
    )
    who = parser.add_argument_group("キャラクター")
    who.add_argument("--badge", help="バッジ（例: 57, 2B, EZ1, ENE）。作品コード付き（NTS-57, SAR-EZ1）なら --work は不要。"
                                     "番号と作品内の識別子（2-alt, Mouse）も使える")
    who.add_argument("--name", help="名前の一部（例: スフィリア）")
    who.add_argument("--work", help="作品キー（例: NumberTales, SinisterChangingGirls）")
    who.add_argument("--form", choices=dataset.FORMS, help="変形前の形態（形態を持つ作品のみ。既定 humanoid）")
    who.add_argument("--height-cm", type=float, help="身長（cm）を上書きする。DB に身長が無いキャラクター向け")
    who.add_argument("--joint", metavar="ID:形状,ID:形状", help="複数キャラクターを 1 枚に描く（例: 85:0,73:3。2〜4 体）")
    who.add_argument("--natural", metavar="依頼文", help="自然文の依頼からキャラクターと形状を読み取る")

    shape = parser.add_argument_group("形状")
    shape.add_argument("--mode", type=int, help=f"形状番号 0〜{MODE_COUNT - 1}（--list-modes で一覧）")
    shape.add_argument("--amount", type=float, default=1.0, help="変形量 0〜1。1 未満は途中の姿（layout=single のみ）")
    shape.add_argument("--size", type=float, default=1.0, help="形状の大きさ 0.2〜2（シェーダーの _SCSize）")
    shape.add_argument("--arms", choices=LIMB_CHOICES, help="腕の扱い（既定はシェーダーの既定値）")
    shape.add_argument("--legs", choices=LIMB_CHOICES, help="脚の扱い（既定はシェーダーの既定値）")
    shape.add_argument("--appendages", choices=APPENDAGE_CHOICES, help="耳と尾の扱い（既定 keep: 形を保つ）")

    picture = parser.add_argument_group("絵作り")
    picture.add_argument("--layout", choices=LAYOUTS, default="single",
                         help="single: 1 体 / sequence: 変形前→途中→完了 / five-view: 五方向図")
    picture.add_argument("--scene", default="", help="場面・背景の指定")
    picture.add_argument("--style", default="", help="画風の指定（既定は参照画像に合わせる）")
    picture.add_argument("--extra", default="", help="追加の作画指定")
    picture.add_argument("--ref", action="append", default=[], metavar="PATH",
                         help="追加の参照画像（references/ か outputs/ の中。複数指定可）")
    picture.add_argument("--no-refs", action="store_true", help="公式画像を参照に添付しない")

    run = parser.add_argument_group("実行")
    run.add_argument("--provider", choices=PROVIDERS, default="gemini")
    run.add_argument("--count", type=int, default=1, help="完成画像の枚数（1〜4）")
    run.add_argument("--rough-count", type=int, help="Stage 3 のラフの枚数（1〜5。既定は単体 3、合同は 1 体あたり 2）")
    run.add_argument("--iterate-from", default="", metavar="実行フォルダー|画像",
                     help="以前の生成結果を起点に改稿する（outputs/ の実行フォルダー名か、その中の画像）")
    run.add_argument("--quick", action="store_true", help="検査なしで 1 回だけ生成する（Stage 1 のプロンプトをそのまま使う）")
    run.add_argument("--skip-canva", action="store_true", help="Stage 5 の Canva 仕上げを省く")
    run.add_argument("--revisions", default="", help="再生成で変えた点（; 区切り）")
    run.add_argument("--retry-of", default="", help="再生成元の実行フォルダー（記録用）")
    run.add_argument("--out", help="出力ベース（既定 outputs/）")
    run.add_argument("--dry-run", action="store_true", help="生成せず、プロンプトと参照画像を表示する")
    run.add_argument("--list-modes", action="store_true", help="形状の一覧と寸法を表示する")
    run.add_argument("--list-characters", action="store_true", help="AI 利用が許可されたキャラクターを表示する")
    return parser


@dataclass
class Subject:
    """描く対象 1 体ぶんの入力。"""

    record: dict[str, Any]
    gate: dict[str, Any]
    character: dict[str, Any]
    spec: dict[str, Any]
    references: list[dict[str, Any]]
    prompt: str

    @property
    def label(self) -> str:
        return f"{self.character['name_ja']} → Mode {self.spec['mode']:02d} {self.spec['name_ja']}"

    @property
    def slug(self) -> str:
        return f"mode{self.spec['mode']:02d}_{self.character['slug']}"


@dataclass
class Plan:
    """実行前に決まること一式（--dry-run はここまでを表示する）。"""

    args: argparse.Namespace
    argv: list[str]
    source: ShaderSource
    subjects: list[Subject]
    shared_references: list[dict[str, Any]]
    revisions: list[str]
    output_base: Path
    retry_of: str | None

    @property
    def joint(self) -> bool:
        return len(self.subjects) > 1

    @property
    def aspect(self) -> str:
        return "3:2" if self.joint else LAYOUT_ASPECT[self.args.layout]

    @property
    def label(self) -> str:
        return " / ".join(subject.label for subject in self.subjects)

    @property
    def run_label(self) -> str:
        if self.joint:
            return "joint_" + "_".join(subject.character["slug"] for subject in self.subjects)
        return self.subjects[0].slug

    @property
    def rough_count(self) -> int:
        return self.args.rough_count or (2 if self.joint else 3)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_modes or args.list_characters:
        return args
    if args.joint:
        if args.badge or args.name or args.mode is not None:
            parser.error("--joint は --badge / --name / --mode と一緒に指定できません。")
        if args.layout != "single" or args.amount != 1.0 or args.quick:
            parser.error("--joint は --layout single・変形完了・5 段階だけに対応しています。")
    elif args.mode is None:
        parser.error("--mode を指定してください（--list-modes で一覧）。")
    if not 0.0 < args.amount <= 1.0:
        parser.error("--amount は 0 より大きく 1 以下で指定してください。")
    if args.amount < 1.0 and args.layout != "single":
        parser.error("--amount は --layout single と組み合わせてください。")
    if not 1 <= args.count <= 4:
        parser.error("--count は 1〜4 で指定してください。")
    if args.rough_count is not None and not 1 <= args.rough_count <= 5:
        parser.error("--rough-count は 1〜5 で指定してください。")
    return args


def expand_natural(argv: list[str], ask: Callable[..., dict[str, Any]] | None = None
                   ) -> tuple[list[str], dict[str, Any] | None]:
    """``--natural 依頼文`` を、読み取った引数に置き換える。無ければそのまま返す。"""
    if "--natural" not in argv:
        return argv, None
    from .natural_parser import parse_request

    index = argv.index("--natural")
    if index + 1 >= len(argv):
        raise PipelineInputError("--natural に依頼文を指定してください。")
    rest = argv[:index] + argv[index + 2:]
    work = None                                   # CLI は「--work X」、MCP ツールは「--work=X」で渡す
    for position, arg in enumerate(rest):
        if arg == "--work" and position + 1 < len(rest):
            work = rest[position + 1]
        elif arg.startswith("--work="):
            work = arg.split("=", 1)[1]
    try:
        parsed = parse_request(argv[index + 1], ask, work)
    except (ValueError, ProviderError, dataset.CharacterLookupError) as err:
        raise PipelineInputError(f"自然文の依頼を読み取れませんでした: {err}") from err
    return parsed["argv"] + rest, parsed["interpretation"]


def _iterate_source(value: str, output_base: Path) -> tuple[Path, str]:
    """``--iterate-from`` を (起点の画像, 実行フォルダー名) に解決する。"""
    path = Path(value)
    if not path.is_absolute() and not path.exists():
        path = output_base / value
    path = path.resolve()
    if not path.is_relative_to(output_base.resolve()):
        raise ValueError(f"--iterate-from は outputs/ の中の実行フォルダーか画像を指定してください: {value}")
    if path.is_file():
        run_dir = next((parent for parent in path.parents if (parent / "run_meta.json").is_file()), path.parent)
        return path, run_dir.name
    meta_file = path / "run_meta.json"
    if not meta_file.is_file():
        raise ValueError(f"--iterate-from の実行フォルダーが見つかりません: {value}")
    results = json.loads(meta_file.read_text(encoding="utf-8")).get("results") or []
    if not results:
        raise ValueError(f"--iterate-from の実行には完成画像がありません: {value}")
    return path / results[0]["file"], path.name


def build_plan(args: argparse.Namespace, argv: list[str] | None = None) -> Plan:
    """Stage 1: キャラクターと形状を確定し、参照画像とプロンプトを組み立てる。API は呼ばない。"""
    try:
        source = load_shader_source()
        output_base = Path(args.out).resolve() if args.out else default_output_base()
        revisions = [item.strip() for item in args.revisions.split(";") if item.strip()]
        shared = resolve_extra_references(args.ref, output_base)
        retry_of = args.retry_of or None
        if args.iterate_from:
            image, run_name = _iterate_source(args.iterate_from, output_base)
            shared.insert(0, {"path": image, "rel": str(image), "role": ITERATE_ROLE, "official": False})
            retry_of = retry_of or run_name

        if args.joint:
            pairs = [item.strip().rsplit(":", 1) for item in args.joint.split(",") if item.strip()]
            if not 2 <= len(pairs) <= 4 or any(len(pair) != 2 or not pair[1].isdigit() for pair in pairs):
                raise ValueError("--joint は「ID:形状番号」を 2〜4 件、カンマ区切りで指定してください（例: 85:0,73:3）。")
            targets = [(dataset.find_any(token, args.work), int(mode)) for token, mode in pairs]
        else:
            targets = [(dataset.find_character(badge=args.badge, name=args.name, work_key=args.work), args.mode)]

        subjects: list[Subject] = []
        for record, mode in targets:
            character = dataset.summarize_character(record, args.form)
            if args.height_cm and not args.joint:
                character.update(height_cm=args.height_cm, height_note="実行時の指定")
            spec = build_shape_spec(
                mode, source, height_cm=character["height_cm"], size=args.size,
                arms=args.arms, legs=args.legs, appendages=args.appendages,
            )
            official = [] if args.no_refs else dataset.collect_reference_images(record, character["form"])
            if args.joint:       # 1 体ずつのラフは場面なしで描き、場面と改稿の起点は合同の清書で使う
                references = official
                prompt = build_prompt(character, spec, references=references, style=args.style)
            else:
                iterate = [ref for ref in shared if ref["role"] == ITERATE_ROLE]
                references = iterate + official + [ref for ref in shared if ref["role"] != ITERATE_ROLE]
                prompt = build_prompt(
                    character, spec, layout=args.layout, amount=args.amount, references=references,
                    scene=args.scene, style=args.style, extra=args.extra, revisions=revisions,
                )
            subjects.append(Subject(record=record, gate=dataset.generation_gate(record), character=character,
                                    spec=spec, references=references, prompt=prompt))
    except (dataset.CharacterLookupError, ShapeSourceError, FileNotFoundError, ValueError) as err:
        raise PipelineInputError(str(err)) from err
    return Plan(args=args, argv=list(argv or []), source=source, subjects=subjects, shared_references=shared,
                revisions=revisions, output_base=output_base, retry_of=retry_of)


def _relative(paths: list[Path], run_dir: Path) -> list[str]:
    return [path.relative_to(run_dir).as_posix() for path in paths]


def _subject_meta(subject: Subject) -> dict[str, Any]:
    character, spec = subject.character, subject.spec
    return {
        "character": {k: character[k] for k in
                      ("name_ja", "name_en", "work_key", "db_key", "identity", "form", "height_cm", "height_note")},
        "ai_training_gate": subject.gate,
        "shape": {
            "mode": spec["mode"], "key": spec["key"], "name_ja": spec["name_ja"], "size": spec["size"],
            "limbs": spec["limbs"], "appendages": spec["appendages"], "dimensions": spec["dimensions"],
            "dimensions_cm": spec["dimensions_cm"], "face": spec["face"], "before": spec["before"],
        },
        "reference_images": [{"path": ref["rel"], "role": ref["role"]} for ref in subject.references],
    }


def execute(
    plan: Plan,
    *,
    generate: Generator | None = None,
    vision: Vision | None = None,
    confirm: Confirm | None = None,
    on_stage: StageCallback | None = None,
) -> dict[str, Any]:
    """生成して記録を残し、結果の要約を返す。

    ``generate`` / ``vision`` はテスト用の差し替え口。``confirm`` は Stage 2 の観察失敗時に
    続行可否を返す関数（既定は端末で尋ねる）。``on_stage`` は Stage 3・4 の完了通知。
    """
    args = plan.args
    generator, model = (generate, "injected") if generate else _provider(args.provider)
    if vision is None:
        from ..openai import vision as vision_module
        vision, vision_model = vision_module.ask_json, vision_module.resolve_model()
    else:
        vision_model = "injected"

    run_dir = build_run_dir(args.provider, plan.run_label, plan.output_base)
    stem = plan.run_label if not plan.joint else "joint"
    meta = {
        "run_id": run_dir.name,
        "pipeline": "quick" if args.quick else "five-stage",
        "argv": plan.argv,
        "service": args.provider,
        "model": model,
        "inspection_model": None if args.quick else vision_model,
        "subjects": [_subject_meta(subject) for subject in plan.subjects],
        "layout": args.layout,
        "amount": args.amount,
        "aspect": plan.aspect,
        "count": args.count,
        "rough_count": None if args.quick else plan.rough_count,
        "scene": args.scene,
        "style": args.style,
        "extra": args.extra,
        "revisions": plan.revisions,
        "retry_of": plan.retry_of,
        "shared_reference_images": [{"path": ref["rel"], "role": ref["role"]} for ref in plan.shared_references],
        "sources": _source_meta(plan.source),
        "prompt_file": "prompt.txt",
    }
    first = plan.subjects[0]
    initialize_run_logs(
        run_dir, prompt_text=first.prompt, meta=meta,
        character="、".join(subject.character["name_ja"] for subject in plan.subjects),
        shape="、".join(f"Mode {subject.spec['mode']:02d} {subject.spec['name_ja']}" for subject in plan.subjects),
        layout=args.layout,
    )
    print(f"[INFO] 実行フォルダー: {run_dir}")
    summary: dict[str, Any] = {"run_id": run_dir.name, "run_dir": str(run_dir), "label": plan.label,
                               "status": "failed", "finals": [], "errors": [], "stages": {}}

    def finish(status: str, finals: list[Path], errors: list[str], extra: dict[str, Any]) -> dict[str, Any]:
        finalize_run_logs(run_dir, status=status, errors=errors,
                          results=[{"file": name, "status": "ok"} for name in _relative(finals, run_dir)],
                          extra=extra)
        summary.update(status=status, finals=[str(path) for path in finals], errors=errors)
        return summary

    def notify(stage: str, paths: list[Path]) -> None:
        summary["stages"].setdefault(stage, []).extend(str(path) for path in paths)
        if on_stage:
            try:
                on_stage(stage, paths, run_dir)
            except Exception as err:  # noqa: BLE001 — 通知の失敗で生成を止めない
                print(f"[WARN] {stage} の通知に失敗しました: {err}")

    stages: dict[str, Any] = {"stage2": [], "stage3": [], "stage4": []}
    try:
        if args.quick:
            result = generator(first.prompt, references=first.references, out_dir=run_dir,
                               stem=stem, aspect=plan.aspect, count=args.count)
            missing = args.count - len(result.files)
            return finish("partial" if missing > 0 else "ok", result.files,
                          [f"{missing} 枚が生成されませんでした。"] if missing > 0 else [],
                          {"model": result.model, "api_mode": result.api_mode})

        best: list[tuple[Subject, str, list[Path]]] = []      # (対象, 観察ブロック, Stage 4 を通った画像)
        prompts: list[str] = []
        for index, subject in enumerate(plan.subjects, start=1):
            root = run_dir / f"char_{subject.character['slug']}_{index:02d}" if plan.joint else run_dir
            name = subject.character["name_ja"]
            print(f"[INFO] Stage 2: {name} の公式設定画を観察します。")
            design = observe_design(subject.character, subject.references, root / "stage2_reference",
                                    vision=vision, confirm=confirm)
            block = observation_block(design)
            prompt = subject.prompt + ("\n" + block + "\n" if block else "")
            prompts.append(prompt)

            print(f"[INFO] Stage 3: {name} のラフを {plan.rough_count} 枚生成します。")
            rough = generator(prompt, references=subject.references, out_dir=root / "stage3_rough",
                              stem="rough", aspect=LAYOUT_ASPECT[args.layout], count=plan.rough_count)
            model, api_mode = rough.model, rough.api_mode
            notify("stage3", rough.files)

            print(f"[INFO] Stage 4: {name} のラフを検査します。")
            stage4 = correct_roughs(
                rough.files, prompt=prompt, character=subject.character, spec=subject.spec, layout=args.layout,
                amount=args.amount, design=design, references=subject.references,
                stage_dir=root / "stage4_correct", aspect=LAYOUT_ASPECT[args.layout],
                generator=generator, vision=vision,
            )
            notify("stage4", stage4["all"])
            best.append((subject, block, stage4["all"]))
            stages["stage2"].append({"character": name, "fallback": design["fallback"],
                                     "observations": len(design["observations"]),
                                     "conflicts": design["conflicts"], "sources": design["sources"]})
            stages["stage3"] += _relative(rough.files, run_dir)
            stages["stage4"].append({"character": name, "files": _relative(stage4["all"], run_dir),
                                     "actions": [entry["action"] for entry in stage4["log"]],
                                     "log": (root / "stage4_correct" / "analysis_log.json")
                                     .relative_to(run_dir).as_posix()})

        if plan.joint:
            roughs = [rough_reference(images[0], f"検査を通ったラフ: {subject.character['name_ja']}")
                      for subject, _, images in best]
            references = plan.shared_references + [ref for subject in plan.subjects for ref in subject.references[:1]]
            final_prompt = build_joint_prompt(
                [(subject.character, subject.spec, block) for subject, block, _ in best],
                references=roughs + references, scene=args.scene, style=args.style, extra=args.extra,
                revisions=plan.revisions,
            )
            header = JOINT_HEADER
            title = "ShapeChange " + " + ".join(subject.character["name_ja"] for subject in plan.subjects)
        else:
            roughs = [rough_reference(path) for path in best[0][2][:2]]
            references, final_prompt, header = first.references, prompts[0], SINGLE_HEADER
            title = f"ShapeChange {first.character['name_ja']} Mode {first.spec['mode']:02d}"
        (run_dir / "prompt.txt").write_text(final_prompt, encoding="utf-8")

        print(f"[INFO] Stage 5: 完成画像を {args.count} 枚清書します。")
        stage5 = generate_finals(
            roughs, header=header, prompt=final_prompt, references=references,
            stage_dir=run_dir / "stage5_final", stem=stem, aspect=plan.aspect, count=args.count,
            generator=generator, skip_canva=args.skip_canva, title=title,
            fallback_to_rough=not plan.joint,
        )
    except ReferenceCancelled as err:
        return finish("cancelled", [], [str(err)], {})
    except ProviderError as err:
        print(f"[ERROR] {err}\n  プロンプトと条件は {run_dir} に残しました。", file=sys.stderr)
        return finish("failed", [], [str(err)], {})

    finals: list[Path] = stage5["all"]
    errors = [note for note in stage5["notes"] if "失敗" in note]
    if len(finals) < args.count:
        errors.append(f"完成画像が {args.count - len(finals)} 枚足りません。")
    stages["stage5"] = {"synth": _relative(stage5["synth"], run_dir),
                        "canva": _relative(stage5["canva"], run_dir), "notes": stage5["notes"]}
    return finish("partial" if errors else "ok", finals, errors,
                  {"model": model, "api_mode": api_mode, "stages": stages})


def run_pipeline(argv: list[str], *, ask: Callable[..., dict[str, Any]] | None = None, **hooks: Any) -> dict[str, Any]:
    """引数リストから計画を立てて実行する（MCP サーバーなど、プログラムからの入口）。

    ``--natural`` を読み替えた場合は、要約の ``interpretation`` に読み取り結果を入れる。
    """
    _load_env()
    argv, interpretation = expand_natural(list(argv), ask)
    try:
        args = parse_args(argv)
    except SystemExit as err:
        raise PipelineInputError("引数を解釈できません。") from err
    summary = execute(build_plan(args, argv), **hooks)
    if interpretation:
        summary["interpretation"] = interpretation
    return summary


def run(argv: list[str] | None = None, *, ask: Callable[..., dict[str, Any]] | None = None, **hooks: Any) -> int:
    """CLI の入口。終了コード: 0 = 生成できた / 1 = 失敗・中止 / 2 = 指定の誤り・利用不可。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    _load_env()
    try:
        argv, interpretation = expand_natural(list(sys.argv[1:] if argv is None else argv), ask)
        args = parse_args(argv)
        if args.list_characters:
            _print_characters(args.work)
            return 0
        if args.list_modes:
            _print_modes(load_shader_source())
            return 0
        plan = build_plan(args, argv)
    except (PipelineInputError, ShapeSourceError) as err:
        print(f"[ERROR] {err}", file=sys.stderr)
        return 2

    if interpretation:
        for item in interpretation["subjects"]:
            print(f"[INFO] 依頼文の読み取り: {item['character']} → Mode {item['mode']:02d} {item['shape']}")
        print(f"[INFO] {interpretation['note']}")
    for subject in plan.subjects:
        for warning in subject.spec["warnings"]:
            print(f"[WARN] {warning}")
        if subject.character["height_cm"] is None:
            print(f"[WARN] {subject.character['name_ja']} は身長が DB に無いため、寸法は比率だけで指定します。")
        if not subject.references:
            print(f"[WARN] {subject.character['name_ja']} は参照画像なしで生成します。")
    model = "injected" if hooks.get("generate") else _provider(args.provider)[1]
    print(f"[INFO] {plan.label}（{args.layout} / {args.provider} / {model}）")
    if args.quick:
        print(f"[INFO] 単発生成（--quick）: {args.count} 枚")
    else:
        per = "1 体あたり " if plan.joint else ""
        print(f"[INFO] 5 段階: ラフ {per}{plan.rough_count} 枚 → 検査と修正 → 完成 {args.count} 枚"
              f"（Canva 仕上げ: {'しない' if args.skip_canva else '連携が設定されていれば行う'}）")
    for subject in plan.subjects:
        for index, ref in enumerate(subject.references, start=1):
            print(f"[INFO] {subject.character['name_ja']} 参照{index}: {ref['role']} — {ref['rel']}")
    if args.dry_run:
        print("[INFO] --dry-run のため生成しません。以下が Stage 1 のプロンプトです。\n")
        for subject in plan.subjects:
            print(subject.prompt)
        return 0

    summary = execute(plan, **hooks)
    for path in summary["finals"]:
        print(f"[OK] 保存: {path}")
    for error in summary["errors"]:
        print(f"[WARN] {error}")
    if summary["status"] in ("ok", "partial"):
        print("[INFO] 元設定と見比べて notes.md に確認結果を記入してください。")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(run())
