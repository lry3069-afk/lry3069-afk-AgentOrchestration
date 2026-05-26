"""Saved View management with workspace-scoped sharing."""

import uuid
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.agent.registry import AuthContext
from src.common.errors import AuthorizationError


@dataclass
class SavedView:
    """A saved view snapshot that can be shared across workspaces."""

    view_id: str
    name: str
    config: Dict[str, Any]
    workspace_id: str
    owner_principal_id: str
    created_at: float
    shared_with: List[str] = field(default_factory=list)  # workspace_ids


class SavedViewManager:
    """Manages saved views and enforces workspace membership on sharing.

    Issue #4687 — Enforce membership on saved view sharing:
    A saved view belongs to a workspace. When an actor attempts to share
    a saved view with another workspace, the manager must verify the actor
    is a member of the workspace that owns the view.
    """

    def __init__(self):
        self._views: Dict[str, SavedView] = {}

    # ── Core CRUD ────────────────────────────────────────────────────────────

    def save(
        self,
        name: str,
        config: Dict[str, Any],
        auth: AuthContext,
        workspace_id: Optional[str] = None,
    ) -> str:
        """Save a new view. Caller must have admin/editor role in workspace."""
        self._check_can_write(auth, workspace_id or auth.workspace_id)

        view_id = str(uuid.uuid4())
        view = SavedView(
            view_id=view_id,
            name=name,
            config=config,
            workspace_id=workspace_id or auth.workspace_id,
            owner_principal_id=auth.principal_id or "anonymous",
            created_at=time.time(),
            shared_with=[],
        )
        self._views[view_id] = view
        return view_id

    def get(self, view_id: str, auth: AuthContext) -> Optional[SavedView]:
        """Get a view by ID. Caller must be in the same workspace."""
        view = self._views.get(view_id)
        if view is None:
            return None
        if view.workspace_id != auth.workspace_id:
            raise AuthorizationError(
                f"View {view_id} belongs to workspace {view.workspace_id}; "
                f"principal is in {auth.workspace_id}"
            )
        return view

    def list_views(
        self, auth: AuthContext, workspace_id: Optional[str] = None
    ) -> List[SavedView]:
        """List views in a workspace, including views shared with it."""
        target = workspace_id or auth.workspace_id
        owned = [v for v in self._views.values() if v.workspace_id == target]
        shared = [v for v in self._views.values() if target in v.shared_with]
        return list({v.view_id: v for v in owned + shared}.values())

    # ── Sharing ─────────────────────────────────────────────────────────────

    def share_with(
        self,
        view_id: str,
        target_workspace_id: str,
        auth: AuthContext,
    ) -> None:
        """Share a saved view with another workspace.

        Raises AuthorizationError if the actor is not a member of the
        workspace that owns the saved view (Issue #4687).
        """
        view = self._views.get(view_id)
        if view is None:
            raise AuthorizationError(f"View {view_id} not found")

        # Issue #4687: membership enforcement — only a member of the
        # owning workspace may share this view
        if auth.workspace_id != view.workspace_id:
            raise AuthorizationError(
                f"Cannot share view {view_id}: actor belongs to "
                f"workspace {auth.workspace_id}, but view is owned by "
                f"workspace {view.workspace_id}"
            )

        if target_workspace_id == view.workspace_id:
            raise AuthorizationError("Cannot share a view with its own workspace")

        if target_workspace_id not in view.shared_with:
            view.shared_with.append(target_workspace_id)

    def revoke_share(
        self,
        view_id: str,
        target_workspace_id: str,
        auth: AuthContext,
    ) -> None:
        """Revoke a workspace's access to a shared view.

        Raises AuthorizationError if the actor is not a member of the
        owning workspace (Issue #4687).
        """
        view = self._views.get(view_id)
        if view is None:
            raise AuthorizationError(f"View {view_id} not found")

        if auth.workspace_id != view.workspace_id:
            raise AuthorizationError(
                f"Cannot revoke share on view {view_id}: actor belongs to "
                f"workspace {auth.workspace_id}, but view is owned by "
                f"workspace {view.workspace_id}"
            )

        if target_workspace_id in view.shared_with:
            view.shared_with.remove(target_workspace_id)

    # ── Internal helpers ────────────────────────────────────────────────────

    def _check_can_write(self, auth: AuthContext, workspace_id: str) -> None:
        """Raise AuthorizationError if auth context cannot write in workspace."""
        if auth.is_anonymous:
            raise AuthorizationError("Anonymous principals cannot save views")
        if auth.is_stale():
            raise AuthorizationError("Stale credentials — re-authenticate and retry")
        if auth.workspace_id != workspace_id:
            raise AuthorizationError(
                f"Principal is in workspace {auth.workspace_id}; "
                f"cannot write to workspace {workspace_id}"
            )
        if not auth.can_mutate():
            raise AuthorizationError(
                f"Role '{auth.role.value}' cannot save views (admin/editor required)"
            )
