"""生成プロバイダの呼び出し方の回帰テスト（SDK を偽物に差し替え、API は呼ばない）。

    python tests/test_providers.py
    python -m pytest tests/
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.utils.provider import ProviderError  # noqa: E402

# 1×1 px の正しい PNG（参照画像は実際に画像として読めるものだけ添付される）
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
_ENV_KEYS = ("OPENAI_API_KEY", "GPT_MODEL", "OPENAI_IMAGE_MODEL", "OPENAI_IMAGE_QUALITY", "DALLE_MODEL",
             "GEMINI_API_KEY", "GEMINI_IMAGE_MODEL", "GEMINI_REFERENCE_MODEL", "GEMINI_IMAGE_SLEEP")


class _Env:
    """テスト中だけ環境変数を差し替える。"""

    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        self.saved = {key: os.environ.pop(key, None) for key in _ENV_KEYS}
        os.environ.update(self.values)

    def __exit__(self, *exc):
        for key in _ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update({k: v for k, v in self.saved.items() if v is not None})


def _reference(folder: Path) -> list[dict]:
    path = folder / "ref.png"
    path.write_bytes(_PNG)
    return [{"path": path, "rel": "ref.png", "role": "公式設定画"}]


def _fake_openai(calls: list):
    class Images:
        def generate(self, **kwargs):
            calls.append(("generate", kwargs))
            return types.SimpleNamespace(data=[types.SimpleNamespace(b64_json=base64.b64encode(_PNG).decode())])

        def edit(self, **kwargs):
            calls.append(("edit", kwargs))
            return types.SimpleNamespace(data=[types.SimpleNamespace(b64_json=base64.b64encode(_PNG).decode())])

    module = types.ModuleType("openai")
    module.OpenAI = lambda api_key: types.SimpleNamespace(images=Images())
    return module


def test_openai_uses_edit_with_references_and_own_model_setting():
    from src.openai import generate as provider

    calls: list = []
    sys.modules["openai"] = _fake_openai(calls)
    try:
        with tempfile.TemporaryDirectory() as tmp, _Env(
            OPENAI_API_KEY="k", OPENAI_IMAGE_QUALITY="standard", DALLE_MODEL="dall-e-3"
        ):
            out = Path(tmp)
            with_refs = provider.generate_images("p", references=_reference(out), out_dir=out, stem="a", aspect="3:2")
            without = provider.generate_images("p", references=[], out_dir=out, stem="b")
            assert with_refs.api_mode == "images.edit" and without.api_mode == "images.generate"
            assert with_refs.files[0].name == "a_01.png" and with_refs.files[0].read_bytes() == _PNG
        (kind1, edit), (kind2, plain) = calls
        assert (kind1, kind2) == ("edit", "generate")
        assert edit["model"] == provider.DEFAULT_MODEL   # 廃止済みの DALLE_MODEL は読まない
        assert edit["size"] == "1536x1024" and plain["size"] == "1024x1024"
        assert edit["quality"] == "medium"                 # DALL-E 時代の standard を読み替える
        assert len(edit["image"]) == 1 and "image" not in plain
        with _Env(OPENAI_IMAGE_MODEL="gpt-image-custom"):
            assert provider.resolve_model() == "gpt-image-custom"
    finally:
        del sys.modules["openai"]


def test_unreadable_reference_is_dropped_with_a_warning():
    import contextlib
    import io

    from src.utils.provider import load_references

    with tempfile.TemporaryDirectory() as tmp:
        broken = Path(tmp) / "broken.png"
        broken.write_bytes(b"not an image")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            loaded = load_references(_reference(Path(tmp)) + [{"path": broken, "rel": "broken.png", "role": "x"}])
    assert [ref["rel"] for ref, _, _ in loaded] == ["ref.png"] and "broken.png" in out.getvalue()


def test_vision_sends_labelled_images_and_requires_a_json_object():
    from src.openai import vision

    calls: list = []
    answers = ['{"observations": ["尾は 7 本"]}', '["not", "an", "object"]']

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            message = types.SimpleNamespace(content=answers.pop(0))
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    module = types.ModuleType("openai")
    module.OpenAI = lambda **kw: types.SimpleNamespace(chat=types.SimpleNamespace(completions=Completions()))
    sys.modules["openai"] = module
    try:
        with tempfile.TemporaryDirectory() as tmp, _Env(OPENAI_API_KEY="k"):
            ref = _reference(Path(tmp))[0]
            assert vision.ask_json("見て", [("公式画像", ref["path"])]) == {"observations": ["尾は 7 本"]}
            try:
                vision.ask_json("見て", [("公式画像", ref["path"])])
            except ProviderError:
                pass
            else:
                raise AssertionError("JSON オブジェクトでない応答が通った")
        content = calls[0]["messages"][0]["content"]
        assert [part["type"] for part in content] == ["text", "text", "image_url"]
        assert content[1]["text"] == "公式画像" and content[2]["image_url"]["url"].startswith("data:image/png;base64,")
        assert calls[0]["model"] == vision.DEFAULT_MODEL and calls[0]["response_format"] == {"type": "json_object"}
    finally:
        del sys.modules["openai"]


def test_missing_key_is_reported_before_calling_the_api():
    from src.openai import generate as provider

    calls: list = []
    sys.modules["openai"] = _fake_openai(calls)
    try:
        with tempfile.TemporaryDirectory() as tmp, _Env():
            try:
                provider.generate_images("p", references=[], out_dir=Path(tmp), stem="a")
            except ProviderError as err:
                assert "OPENAI_API_KEY" in str(err) and not calls
            else:
                raise AssertionError("API キーなしで例外にならなかった")
    finally:
        del sys.modules["openai"]


def _fake_genai(calls: list, responses: list):
    class Part:
        @staticmethod
        def from_text(text):
            return ("text", text)

        @staticmethod
        def from_bytes(data, mime_type):
            return ("bytes", mime_type, len(data))

    class Models:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            outcome = responses.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            part = types.SimpleNamespace(inline_data=types.SimpleNamespace(data=outcome))
            return types.SimpleNamespace(candidates=[types.SimpleNamespace(content=types.SimpleNamespace(parts=[part]))])

    types_module = types.ModuleType("google.genai.types")
    types_module.Part = Part
    types_module.Modality = types.SimpleNamespace(IMAGE="IMAGE")
    types_module.ImageConfig = lambda **kw: kw
    types_module.GenerateContentConfig = lambda **kw: kw
    genai = types.ModuleType("google.genai")
    genai.Client = lambda api_key: types.SimpleNamespace(models=Models())
    genai.types = types_module
    google = types.ModuleType("google")
    google.genai = genai
    return {"google": google, "google.genai": genai, "google.genai.types": types_module}


def test_gemini_attaches_labelled_references_and_keeps_partial_results():
    from src.gemini import generate as provider

    calls: list = []
    fakes = _fake_genai(calls, [_PNG, RuntimeError("500 internal")])
    saved = {name: sys.modules.get(name) for name in fakes}
    sys.modules.update(fakes)
    try:
        with tempfile.TemporaryDirectory() as tmp, _Env(
            GEMINI_API_KEY="k", GEMINI_REFERENCE_MODEL="models/gemini-3.1-flash-image", GEMINI_IMAGE_SLEEP="0"
        ):
            out = Path(tmp)
            result = provider.generate_images("p", references=_reference(out), out_dir=out, stem="g",
                                              aspect="3:2", count=2)
            # 2 枚目が失敗しても 1 枚目は返す（欠けはパイプラインが partial として記録する）
            assert [f.name for f in result.files] == ["g_01.png"]
            assert result.model == "models/gemini-3.1-flash-image"   # GeneratorsAI と同じ設定を引き継ぐ
        contents = calls[0]["contents"]
        assert contents[0] == ("text", "p") and contents[1] == ("text", "[参照1: 公式設定画]")
        assert contents[2][:2] == ("bytes", "image/png")
        assert calls[0]["config"]["image_config"] == {"aspect_ratio": "3:2"}
        with _Env(GEMINI_IMAGE_MODEL="x", GEMINI_REFERENCE_MODEL="y"):
            assert provider.resolve_model() == "x"
        with _Env():
            assert provider.resolve_model() == provider.DEFAULT_MODEL
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def test_gemini_empty_image_sleep_uses_the_default():
    from src.gemini import generate as provider

    calls: list = []
    fakes = _fake_genai(calls, [_PNG, _PNG])
    saved = {name: sys.modules.get(name) for name in fakes}
    sys.modules.update(fakes)
    pauses: list[float] = []
    original_sleep = provider.time.sleep
    provider.time.sleep = pauses.append
    try:
        with tempfile.TemporaryDirectory() as tmp, _Env(GEMINI_API_KEY="k", GEMINI_IMAGE_SLEEP=""):
            result = provider.generate_images("p", references=[], out_dir=Path(tmp), stem="g", count=2)
        assert len(result.files) == 2 and pauses == [6.0]
    finally:
        provider.time.sleep = original_sleep
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


if __name__ == "__main__":
    from _runner import run_module_tests

    run_module_tests(globals())
