"""Compiler detection and C/C++ compilation.

* Compiler paths come from configuration (COMPILER_C_PATH / COMPILER_CPP_PATH)
  or are auto-detected on PATH. Nothing is ever downloaded or installed.
* The compiler is invoked with a strict argument list — untrusted input never
  reaches a shell, and users cannot supply compiler flags or executable paths.
* Compilation runs with a hard timeout and capped output capture.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.constants import Language
from app.core.exceptions import UnsupportedLanguageError
from app.services.filesystem_isolation import Workspace
from app.services.process_support import (
    CREATE_NO_WINDOW,
    build_child_environment,
    start_stream_capture,
    wait_process_deadline,
)

logger = logging.getLogger(__name__)

EXECUTABLE_NAME = "program.exe"

_SOURCE_NAMES = {Language.C: "source.c", Language.CPP: "source.cpp"}

#: Exit status Windows uses when a console program cannot find a DLL.
STATUS_DLL_NOT_FOUND = 0xC0000135
STATUS_DLL_INIT_FAILED = 0xC0000142


@dataclass(frozen=True)
class CompilerInfo:
    language: str
    compiler: str
    available: bool
    path: str | None
    version: str | None
    detail: str

    def as_dict(self) -> dict:
        return {
            "language": self.language,
            "compiler": self.compiler,
            "available": self.available,
            "path": self.path,
            "version": self.version,
            "detail": self.detail,
        }


@dataclass
class CompileOutcome:
    success: bool
    executable_path: Path | None
    output: str
    duration_ms: int
    compiler: CompilerInfo
    timed_out: bool = False
    output_truncated: bool = False
    failure_reason: str | None = None


class CompilationService:
    """Detects compilers once and compiles submissions in their workspace."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._cache: dict[str, CompilerInfo] = {}
        self._lock = threading.Lock()

    # -- detection ----------------------------------------------------------
    def _compiler_spec(self, language: Language) -> tuple[str, str, list[str]]:
        if language == Language.C:
            return "c", "gcc", [p for p in [self.settings.compiler_c_path.strip()] if p]
        if language == Language.CPP:
            return "cpp", "g++", [p for p in [self.settings.compiler_cpp_path.strip()] if p]
        raise UnsupportedLanguageError(f"Unsupported language: {language}")

    def detect(self, language: Language, *, force: bool = False) -> CompilerInfo:
        lang, name, configured = self._compiler_spec(language)
        key = lang
        with self._lock:
            if not force and key in self._cache:
                return self._cache[key]
        info = self._detect_uncached(lang, name, configured)
        with self._lock:
            self._cache[key] = info
        return info

    def _detect_uncached(self, lang: str, name: str, configured: list[str]) -> CompilerInfo:
        if configured:
            path = shutil.which(configured[0]) or (
                configured[0] if Path(configured[0]).is_file() else None
            )
            source = "configured path"
        else:
            path = shutil.which(name)
            source = "PATH lookup"
        if not path:
            return CompilerInfo(
                language=lang,
                compiler=name,
                available=False,
                path=None,
                version=None,
                detail=(
                    f"{name} not found via {source}. Install MinGW-w64 (e.g. "
                    f"https://www.mingw-w64.org) and either add it to PATH or set "
                    f"COMPILER_{lang.upper()}_PATH in .env. CodeGuard never downloads "
                    "compilers automatically."
                ),
            )
        version = self._read_version(Path(path))
        if version is None:
            return CompilerInfo(
                language=lang,
                compiler=name,
                available=False,
                path=str(path),
                version=None,
                detail=f"{path} exists but did not respond to `--version`.",
            )
        return CompilerInfo(
            language=lang,
            compiler=name,
            available=True,
            path=str(path),
            version=version,
            detail=f"Detected via {source}.",
        )

    def _read_version(self, compiler: Path) -> str | None:
        try:
            proc = subprocess.run(
                [str(compiler), "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                env=build_child_environment([str(compiler.parent)]),
                creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        first_line = (proc.stdout or proc.stderr or "").strip().splitlines()
        return first_line[0] if first_line else None

    def detect_all(self, *, force: bool = False) -> dict[str, CompilerInfo]:
        return {
            lang.value: self.detect(lang, force=force)
            for lang in (Language.C, Language.CPP)
        }

    def compiler_bin_dir(self, language: Language) -> str | None:
        info = self.detect(language)
        return str(Path(info.path).parent) if info.available and info.path else None

    # -- compilation --------------------------------------------------------
    def compile(
        self,
        workspace: Workspace,
        source_code: str,
        language: Language,
    ) -> CompileOutcome:
        info = self.detect(language)
        if not info.available or not info.path:
            return CompileOutcome(
                success=False,
                executable_path=None,
                output=info.detail,
                duration_ms=0,
                compiler=info,
                failure_reason="compiler_unavailable",
            )

        size = len(source_code.encode("utf-8"))
        if size > self.settings.max_source_size_bytes:
            return CompileOutcome(
                success=False,
                executable_path=None,
                output=(
                    f"Source code is {size} bytes; the server limit is "
                    f"{self.settings.max_source_size_bytes} bytes."
                ),
                duration_ms=0,
                compiler=info,
                failure_reason="source_too_large",
            )

        source_name = _SOURCE_NAMES[language]
        source_path = workspace.write_text(
            source_name, source_code, max_bytes=self.settings.max_source_size_bytes
        )
        flags = (
            self.settings.compiler_c_flags if language == Language.C
            else self.settings.compiler_cpp_flags
        ).split()
        command = [info.path, *flags, source_path.name, "-o", EXECUTABLE_NAME]

        started = time.monotonic()
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(workspace.path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=build_child_environment([str(Path(info.path).parent)]),
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            return CompileOutcome(
                success=False,
                executable_path=None,
                output=f"Failed to invoke the compiler ({exc.__class__.__name__}).",
                duration_ms=0,
                compiler=info,
                failure_reason="compiler_invocation_failed",
            )

        cap = self.settings.max_output_size_bytes
        stdout_cap, stderr_cap, threads = start_stream_capture(proc, cap)
        wait_result = wait_process_deadline(
            proc, self.settings.max_compilation_seconds * 1000
        )
        timed_out = wait_result != "exited"
        if timed_out:
            proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            proc.kill()
        for t in threads:
            t.join(timeout=1.0)

        duration_ms = int((time.monotonic() - started) * 1000)
        combined = (
            stdout_cap.text + stderr_cap.text
        ).strip()
        truncated = stdout_cap.truncated or stderr_cap.truncated

        executable = workspace.path / EXECUTABLE_NAME
        success = (not timed_out) and proc.returncode == 0 and executable.is_file()
        failure_reason = None
        if timed_out:
            failure_reason = "compilation_timeout"
            combined = (
                f"Compilation exceeded the {self.settings.max_compilation_seconds}s limit "
                "and was terminated.\n" + combined
            )
        elif not success:
            failure_reason = "compiler_error"

        return CompileOutcome(
            success=success,
            executable_path=executable if success else None,
            output=combined,
            duration_ms=duration_ms,
            compiler=info,
            timed_out=timed_out,
            output_truncated=truncated,
            failure_reason=failure_reason,
        )
