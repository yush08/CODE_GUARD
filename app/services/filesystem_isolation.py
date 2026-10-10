"""Per-submission workspace isolation (Windows).

What this module actually provides:

* A unique, predictably named workspace per submission under SANDBOX_ROOT.
* Canonicalized paths with traversal validation (every path is resolved and
  checked to be inside the sandbox root before it is touched or deleted).
* Controlled filenames (strict allow-list, no separators, no leading dots).
* Size limits on written source files.
* Best-effort cleanup with retries (locked executables, AV holds) that NEVER
  deletes anything outside the configured sandbox root.
* Optional Windows ACL hardening via pywin32: the workspace DACL is rewritten
  to grant access to the current user and SYSTEM only, dropping inherited
  ACEs, so other local accounts cannot read student code.

What this module does NOT provide: it does not stop the submitted program
from reading the rest of the file system — the program runs as the same user.
A folder with a restricted DACL is hardening, not a sandbox. See README.
"""

from __future__ import annotations

import logging
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

try:  # pywin32 is optional; every use is capability-reported
    import win32api
    import win32con
    import win32security

    HAS_PYWIN32 = True
except ImportError:  # pragma: no cover - depends on environment
    HAS_PYWIN32 = False

SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_LABEL = re.compile(r"[^A-Za-z0-9_-]+")

#: Files the judge itself creates; excluded from WORKSPACE_FILE_CREATED events.
JUDGE_ARTIFACTS = frozenset({"stdin.txt"})


class WorkspaceError(Exception):
    """Raised when a workspace operation would be unsafe."""


@dataclass
class Workspace:
    """An isolated directory dedicated to one submission."""

    path: Path
    root: Path

    # -- safe path handling -------------------------------------------------
    def safe_path(self, name: str) -> Path:
        """Validate a filename and return its absolute path inside the workspace."""
        if not SAFE_NAME.match(name):
            raise WorkspaceError(f"Unsafe filename rejected: {name!r}")
        candidate = (self.path / name).resolve()
        if not candidate.is_relative_to(self.path.resolve()):
            raise WorkspaceError("Path escapes the submission workspace.")
        return candidate

    def write_text(self, name: str, content: str, *, max_bytes: int | None = None) -> Path:
        target = self.safe_path(name)
        data = content.encode("utf-8")
        if max_bytes is not None and len(data) > max_bytes:
            raise WorkspaceError(f"Content for {name!r} exceeds the size limit.")
        target.write_bytes(data)
        return target

    def snapshot_files(self) -> set[str]:
        """Relative paths of every file currently inside the workspace."""
        base = self.path.resolve()
        return {
            str(p.relative_to(base)).lower()
            for p in base.rglob("*")
            if p.is_file()
        }


class WorkspaceManager:
    """Creates and destroys workspaces under the configured sandbox root."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.root = Path(self.settings.sandbox_root).resolve()

    def create(self, label: str, submission_id: str | int | None = None) -> Workspace:
        self.root.mkdir(parents=True, exist_ok=True)
        safe_label = SAFE_LABEL.sub("_", label).strip("_")[:40] or "sub"
        suffix = uuid.uuid4().hex[:8]
        name = f"sub_{safe_label}_{suffix}" if submission_id is None else f"sub_{submission_id}_{suffix}"
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root):  # pragma: no cover - defensive
            raise WorkspaceError("Computed workspace escapes the sandbox root.")
        path.mkdir(parents=False, exist_ok=False)
        return Workspace(path=path, root=self.root)

    def cleanup(self, workspace: Workspace, *, timeout_seconds: float | None = None) -> tuple[bool, str]:
        """Delete the workspace. Retries briefly on Windows file locks.

        Returns (ok, detail). Refuses to touch anything outside the sandbox
        root — this guard is what prevents a bug from deleting user files.
        """
        timeout = timeout_seconds if timeout_seconds is not None else float(
            self.settings.workspace_cleanup_timeout_seconds
        )
        target = Path(workspace.path).resolve()
        try:
            if not target.is_relative_to(self.root) or target == self.root:
                return False, "Refused: cleanup target is outside the sandbox root."
        except OSError as exc:
            return False, f"Refused: could not canonicalize target ({exc.__class__.__name__})."

        deadline = time.monotonic() + max(timeout, 0.0)
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                shutil.rmtree(target, ignore_errors=False)
                return True, "Workspace removed."
            except FileNotFoundError:
                return True, "Workspace already removed."
            except OSError as exc:
                last_error = exc
                time.sleep(0.15)
        logger.warning(
            "Workspace cleanup failed for %s: %s", target.name, last_error.__class__.__name__
        )
        return False, f"Cleanup failed after {timeout:.0f}s ({last_error.__class__.__name__})."


# ---------------------------------------------------------------------------
# Windows ACL hardening (real, capability-reported)
# ---------------------------------------------------------------------------
_acl_probe_cache: tuple[bool, str] | None = None


def _current_user_sid_string() -> str:
    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32security.TOKEN_QUERY
    )
    sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    return win32security.ConvertSidToStringSid(sid)


def _dacl_from_sddl(sddl: str):
    descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(sddl, 1)
    return descriptor.GetSecurityDescriptorDacl()


def harden_workspace_acl(path: Path) -> tuple[bool, str]:
    """Rewrite the DACL (protected = no inherited ACEs): only the current
    user and SYSTEM keep full control.

    Built from an SDDL descriptor because this pywin32 build does not expose
    EXPLICIT_ACCESS/SetEntriesInAcl. This is real Windows ACL enforcement,
    verified by an integration test that confirms other ACEs are gone.
    """
    if not HAS_PYWIN32:
        return False, "pywin32 is not installed; ACL hardening is unavailable."
    try:
        sid_str = _current_user_sid_string()
        # D: protected DACL; FA = file all access for the user and SYSTEM.
        dacl = _dacl_from_sddl(f"D:P(A;;FA;;;{sid_str})(A;;FA;;;SY)")
        win32security.SetNamedSecurityInfo(
            str(path),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            dacl,
            None,
        )
        return True, "Workspace DACL restricted to the current user and SYSTEM."
    except Exception as exc:  # pywintypes.error and friends
        return False, f"ACL hardening failed ({exc.__class__.__name__})."


def apply_deny_read_current_user(path: Path) -> tuple[bool, str]:
    """Add a DENY-ACE for the current user on one file.

    Used by the security self-test to PROVE that Windows ACLs really deny
    access at the OS level (the test process itself gets PermissionError).
    Cleanup remains possible because the parent folder grants DELETE_CHILD
    to its owner.
    """
    if not HAS_PYWIN32:
        return False, "pywin32 is not installed."
    try:
        sid_str = _current_user_sid_string()
        dacl = _dacl_from_sddl(f"D:(D;;FA;;;{sid_str})(A;;FA;;;{sid_str})(A;;FA;;;SY)")
        win32security.SetNamedSecurityInfo(
            str(path),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION,
            None,
            None,
            dacl,
            None,
        )
        return True, "DENY ACE for the current user applied."
    except Exception as exc:
        return False, f"Could not apply deny ACE ({exc.__class__.__name__})."


def probe_acl_support(root: Path) -> tuple[bool, str]:
    """Check whether ACL hardening works on the sandbox volume."""
    global _acl_probe_cache
    if _acl_probe_cache is not None:
        return _acl_probe_cache
    if not HAS_PYWIN32:
        _acl_probe_cache = (False, "pywin32 is not installed.")
        return _acl_probe_cache
    probe_dir = Path(root) / f"acl_probe_{uuid.uuid4().hex[:6]}"
    try:
        probe_dir.mkdir(parents=True, exist_ok=True)
        ok, detail = harden_workspace_acl(probe_dir)
        # Verify we (the same user) can still create files after hardening.
        if ok:
            (probe_dir / "probe.txt").write_text("probe", encoding="utf-8")
        _acl_probe_cache = (ok, detail)
    except OSError as exc:
        _acl_probe_cache = (False, f"ACL probe failed ({exc.__class__.__name__}).")
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)
    return _acl_probe_cache


def reset_acl_probe_cache() -> None:
    global _acl_probe_cache
    _acl_probe_cache = None
