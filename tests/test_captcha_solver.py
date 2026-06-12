"""Captcha solver / prompt tests (fju spec 2026-06-12)."""

import shutil
import unittest
import uuid
from pathlib import Path

from tron_roll_call_hero.captcha_solver import (
    ConsoleCaptchaPrompt,
    OcrCaptchaSolver,
    clean_captcha_text,
)

TEST_WORKSPACE_DIR = Path(__file__).resolve().parents[1]


def make_temp() -> Path:
    root = TEST_WORKSPACE_DIR / ".tmp-tests"
    root.mkdir(exist_ok=True)
    path = root / uuid.uuid4().hex
    path.mkdir()
    return path


def _engine_module(engine):
    class FakeModule:
        @staticmethod
        def DdddOcr(*_args, **_kwargs):
            return engine

    return FakeModule


class CleanCaptchaTest(unittest.TestCase):
    def test_keeps_alnum_and_strips_noise(self) -> None:
        self.assertEqual(clean_captcha_text("  aB c1 "), "aBc1")

    def test_rejects_too_short_or_long(self) -> None:
        self.assertIsNone(clean_captcha_text("a"))
        self.assertIsNone(clean_captcha_text("abcdefghij"))

    def test_rejects_empty_after_clean(self) -> None:
        self.assertIsNone(clean_captcha_text("  -- "))


class OcrCaptchaSolverTest(unittest.TestCase):
    def test_missing_ddddocr_returns_none_and_marks_unavailable(self) -> None:
        calls = []

        def importer():
            calls.append(1)
            raise ImportError("no ddddocr")

        solver = OcrCaptchaSolver(importer=importer)
        self.assertIsNone(solver.solve(b"img"))
        self.assertIsNone(solver.solve(b"img"))
        # 標記為不可用後不再重試 import
        self.assertEqual(len(calls), 1)

    def test_uses_injected_engine_and_cleans_result(self) -> None:
        class FakeEngine:
            def classification(self, image_bytes):
                return " Ab12 "

        solver = OcrCaptchaSolver(importer=lambda: _engine_module(FakeEngine()))
        self.assertEqual(solver.solve(b"img"), "Ab12")

    def test_engine_exception_returns_none(self) -> None:
        class BoomEngine:
            def classification(self, image_bytes):
                raise RuntimeError("boom")

        solver = OcrCaptchaSolver(importer=lambda: _engine_module(BoomEngine()))
        self.assertIsNone(solver.solve(b"img"))


class ConsoleCaptchaPromptTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.base = make_temp()

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    async def test_non_tty_returns_none(self) -> None:
        prompt = ConsoleCaptchaPrompt(isatty=lambda: False)
        result = await prompt.prompt(b"img", attempt=0, save_path=self.base / "c.jpg")
        self.assertIsNone(result)

    async def test_tty_saves_image_and_reads_input(self) -> None:
        printed = []
        prompt = ConsoleCaptchaPrompt(
            isatty=lambda: True,
            input_func=lambda _prompt: "Ab12",
            printer=printed.append,
        )
        save_path = self.base / "c.jpg"
        result = await prompt.prompt(b"jpeg-bytes", attempt=0, save_path=save_path)
        self.assertEqual(result, "Ab12")
        self.assertTrue(save_path.exists())
        self.assertEqual(save_path.read_bytes(), b"jpeg-bytes")
        self.assertTrue(any(str(save_path) in line for line in printed))

    async def test_blank_input_returns_none(self) -> None:
        prompt = ConsoleCaptchaPrompt(
            isatty=lambda: True, input_func=lambda _prompt: "   "
        )
        result = await prompt.prompt(b"img", attempt=0, save_path=self.base / "c.jpg")
        self.assertIsNone(result)

    async def test_eof_returns_none(self) -> None:
        def boom(_prompt):
            raise EOFError()

        prompt = ConsoleCaptchaPrompt(isatty=lambda: True, input_func=boom)
        result = await prompt.prompt(b"img", attempt=0, save_path=self.base / "c.jpg")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
