r"""Report the local toolchain the judge depends on (no network, no changes).

PowerShell:
    .\.venv\Scripts\python.exe scripts\check_compiler.py

Exits 0 when a C and a C++ compiler were detected, 1 otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.services.compilation_service import CompilationService  # noqa: E402
from app.services.windows_job_manager import probe_job_objects  # noqa: E402


def main() -> int:
    settings = get_settings()
    service = CompilationService(settings)
    ok = True
    for lang, info in service.detect_all().items():
        state = "OK  " if info.available else "FAIL"
        print(f"[{state}] {lang:4} {info.compiler}: {info.detail}")
        if info.path:
            print(f"        path    : {info.path}")
        if info.version:
            print(f"        version : {info.version}")
        ok = ok and info.available

    available, detail = probe_job_objects(force=True)
    print(f"[{'OK  ' if available else 'FAIL'}] Windows Job Objects: {detail}")
    ok = ok and available

    try:
        import ctypes

        elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # pragma: no cover
        elevated = False
    print(
        f"[{'WARN' if elevated else 'OK  '}] server elevation: "
        + ("running as Administrator — do not judge student code this way"
           if elevated else "normal user (recommended)")
    )

    print(f"        database: {settings.database_config_state().value}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
