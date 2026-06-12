"""Captcha answer sources for built-in TronClass login forms (fju spec).

Two injectable sources of a captcha answer:

* :class:`OcrCaptchaSolver` lazily imports the optional ``ddddocr`` package and
  returns ``None`` (never raises) when it is missing or fails.
* :class:`ConsoleCaptchaPrompt` hands the image to a human at an interactive
  CLI; it returns ``None`` in non-interactive environments.

Both are injected, so tests need neither onnxruntime nor a real person. The raw
image bytes and the answer never enter logs, snapshots, or events.

This module must not import ``tron_roll_call_hero.runtime_context``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional, Protocol, runtime_checkable

_MIN_LEN = 3
_MAX_LEN = 8


def clean_captcha_text(raw: Any) -> Optional[str]:
    """Strip to alphanumerics; return ``None`` if the result is an implausible code."""
    text = "".join(ch for ch in str(raw or "") if ch.isalnum())
    if _MIN_LEN <= len(text) <= _MAX_LEN:
        return text
    return None


class OcrCaptchaSolver:
    """ddddocr-backed solver. Optional dependency; degrades to ``None``."""

    def __init__(self, *, importer: Optional[Callable[[], Any]] = None) -> None:
        self._importer = importer if importer is not None else _import_ddddocr
        self._engine: Any = None
        self._unavailable = False

    def _ensure_engine(self) -> Any:
        if self._unavailable:
            return None
        if self._engine is None:
            try:
                module = self._importer()
                self._engine = module.DdddOcr(show_ad=False)
            except Exception:
                self._unavailable = True
                return None
        return self._engine

    def solve(self, image_bytes: bytes) -> Optional[str]:
        engine = self._ensure_engine()
        if engine is None:
            return None
        try:
            raw = engine.classification(image_bytes)
        except Exception:
            return None
        return clean_captcha_text(raw)


def _import_ddddocr() -> Any:  # pragma: no cover - exercised only when installed
    import ddddocr

    return ddddocr


@runtime_checkable
class CaptchaPrompt(Protocol):
    async def prompt(
        self, image_bytes: bytes, *, attempt: int, save_path: Any
    ) -> Optional[str]: ...


class ConsoleCaptchaPrompt:
    """Interactive-CLI prompt. Writes the image to disk, prints the path, reads input."""

    def __init__(
        self,
        *,
        input_func: Callable[[str], str] = input,
        isatty: Optional[Callable[[], bool]] = None,
        printer: Callable[[str], None] = print,
        opener: Optional[Callable[[Path], None]] = None,
    ) -> None:
        self._input = input_func
        self._isatty = isatty if isatty is not None else _stdin_isatty
        self._print = printer
        self._opener = opener

    async def prompt(
        self, image_bytes: bytes, *, attempt: int, save_path: Any
    ) -> Optional[str]:
        if not self._isatty():
            return None
        path = Path(save_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image_bytes)
        except OSError:
            return None
        self._print("驗證碼圖片已存到：{}（第 {} 次嘗試）".format(path, attempt + 1))
        if self._opener is not None:
            try:
                self._opener(path)
            except Exception:
                pass
        try:
            answer = self._input("請輸入圖片中的驗證碼：")
        except (EOFError, KeyboardInterrupt):
            return None
        answer = str(answer or "").strip()
        return answer or None


def _stdin_isatty() -> bool:  # pragma: no cover - environment dependent
    import sys

    try:
        return bool(sys.stdin.isatty())
    except Exception:
        return False
