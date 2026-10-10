"""Local-dev access model.

The initial academic prototype does NOT implement authentication. Every
request is treated as an anonymous local actor whose only identifier is an
optional client-supplied workspace label.

This is explicitly NOT a verified student identity, and no endpoint in the
current build enforces authorization. The structure exists so real
authentication and role-based authorization can be added later without
reshaping the API. Bind the server to 127.0.0.1 (the documented default).
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header

from app.core.config import get_settings

LOCAL_DEFAULT_LABEL = "local-workspace"


@dataclass(frozen=True)
class Actor:
    workspace_label: str
    role: str = "LOCAL_DEV"
    authenticated: bool = False


async def get_actor(
    workspace_label: str | None = Header(default=None, alias="X-Workspace-Label"),
) -> Actor:
    """Resolve the caller. In local_dev mode this never authenticates anyone."""
    settings = get_settings()
    header_name = settings.workspace_label_header
    label = (workspace_label or LOCAL_DEFAULT_LABEL).strip()[:120] or LOCAL_DEFAULT_LABEL
    return Actor(workspace_label=label, role="LOCAL_DEV", authenticated=False)


def header_alias() -> str:
    return get_settings().workspace_label_header
