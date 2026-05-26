"""Regression tests for Issue #4687 — saved view workspace sharing enforcement."""

import pytest
from src.agent.saved_view import SavedViewManager, SavedView
from src.agent.registry import AuthContext, Role
from src.common.errors import AuthorizationError


@pytest.fixture
def svm():
    return SavedViewManager()


@pytest.fixture
def ctx_ws1():
    """Admin auth in workspace ws-1."""
    return AuthContext(workspace_id="ws-1", role=Role.ADMIN, principal_id="user-1")


@pytest.fixture
def ctx_ws2():
    """Admin auth in workspace ws-2."""
    return AuthContext(workspace_id="ws-2", role=Role.ADMIN, principal_id="user-2")


@pytest.fixture
def ctx_ws1_viewer():
    """Viewer auth in workspace ws-1 (cannot mutate)."""
    return AuthContext(workspace_id="ws-1", role=Role.VIEWER, principal_id="user-3")


class TestSavedViewWorkspaceSharing:
    """Issue #4687 — enforce membership on saved view sharing."""

    def test_cross_workspace_share_denied(self, svm, ctx_ws1, ctx_ws2):
        """Actor in ws-2 cannot share a view owned by ws-1."""
        view_id = svm.save(name="my-view", config={"foo": "bar"}, auth=ctx_ws1)

        with pytest.raises(AuthorizationError, match="Cannot share view"):
            svm.share_with(view_id, target_workspace_id="ws-2", auth=ctx_ws2)

    def test_own_workspace_share_allowed(self, svm, ctx_ws1, ctx_ws2):
        """Actor in ws-1 can share their own view with ws-2."""
        view_id = svm.save(name="my-view", config={"foo": "bar"}, auth=ctx_ws1)
        svm.share_with(view_id, target_workspace_id="ws-2", auth=ctx_ws1)

        view = svm.get(view_id, auth=ctx_ws1)
        assert "ws-2" in view.shared_with

    def test_own_workspace_revoke_allowed(self, svm, ctx_ws1, ctx_ws2):
        """Actor in ws-1 can revoke a share they previously granted."""
        view_id = svm.save(name="my-view", config={"foo": "bar"}, auth=ctx_ws1)
        svm.share_with(view_id, target_workspace_id="ws-2", auth=ctx_ws1)
        svm.revoke_share(view_id, target_workspace_id="ws-2", auth=ctx_ws1)

        view = svm.get(view_id, auth=ctx_ws1)
        assert "ws-2" not in view.shared_with

    def test_cross_workspace_revoke_denied(self, svm, ctx_ws1, ctx_ws2):
        """Actor in ws-2 cannot revoke a share on a view owned by ws-1."""
        view_id = svm.save(name="my-view", config={"foo": "bar"}, auth=ctx_ws1)
        svm.share_with(view_id, target_workspace_id="ws-2", auth=ctx_ws1)

        with pytest.raises(AuthorizationError, match="Cannot revoke share"):
            svm.revoke_share(view_id, target_workspace_id="ws-2", auth=ctx_ws2)

    def test_view_invisible_to_other_workspace(self, svm, ctx_ws1, ctx_ws2):
        """A view owned by ws-1 is not readable by ws-2 unless shared."""
        view_id = svm.save(name="secret", config={}, auth=ctx_ws1)

        # Direct get raises
        with pytest.raises(AuthorizationError, match="belongs to workspace"):
            svm.get(view_id, auth=ctx_ws2)

        # But after sharing, it appears in ws-2's list
        svm.share_with(view_id, target_workspace_id="ws-2", auth=ctx_ws1)
        shared = svm.list_views(auth=ctx_ws2)
        assert any(v.view_id == view_id for v in shared)

    def test_anonymous_cannot_save(self, svm, ctx_ws1):
        """Anonymous principals cannot save views."""
        ctx_anon = AuthContext(workspace_id="ws-1", is_anonymous=True)
        with pytest.raises(AuthorizationError, match="Anonymous"):
            svm.save(name="v", config={}, auth=ctx_anon)

    def test_viewer_cannot_save(self, svm, ctx_ws1_viewer):
        """VIEWER role cannot save views."""
        with pytest.raises(AuthorizationError, match="cannot save"):
            svm.save(name="v", config={}, auth=ctx_ws1_viewer)

    def test_stale_credential_cannot_save(self, svm, ctx_ws1):
        """Stale credentials are rejected on save."""
        ctx_ws1._last_checked = ctx_ws1._created_at - 400.0
        with pytest.raises(AuthorizationError, match="Stale"):
            svm.save(name="v", config={}, auth=ctx_ws1)

    def test_share_with_own_workspace_rejected(self, svm, ctx_ws1):
        """Sharing a view with its own workspace is not allowed."""
        view_id = svm.save(name="v", config={}, auth=ctx_ws1)
        with pytest.raises(AuthorizationError, match="Cannot share a view with its own workspace"):
            svm.share_with(view_id, target_workspace_id="ws-1", auth=ctx_ws1)

    def test_list_includes_shared_views(self, svm, ctx_ws1, ctx_ws2):
        """list_views() includes views shared with the workspace."""
        view_id = svm.save(name="shared-view", config={}, auth=ctx_ws1)
        svm.share_with(view_id, target_workspace_id="ws-2", auth=ctx_ws1)

        ws2_views = svm.list_views(auth=ctx_ws2)
        assert any(v.view_id == view_id for v in ws2_views)

    def test_list_excludes_unshared_views(self, svm, ctx_ws1, ctx_ws2):
        """list_views() does not include views not shared with the workspace."""
        view_id = svm.save(name="private", config={}, auth=ctx_ws1)
        ws2_views = svm.list_views(auth=ctx_ws2)
        assert not any(v.view_id == view_id for v in ws2_views)

    def test_editor_can_save(self, svm):
        """EDITOR role can save a view."""
        ctx = AuthContext(workspace_id="ws-1", role=Role.EDITOR, principal_id="user-e")
        vid = svm.save(name="e-view", config={}, auth=ctx)
        assert vid is not None

    def test_duplicate_share_is_idempotent(self, svm, ctx_ws1, ctx_ws2):
        """Sharing the same view twice with the same workspace is idempotent."""
        view_id = svm.save(name="v", config={}, auth=ctx_ws1)
        svm.share_with(view_id, target_workspace_id="ws-2", auth=ctx_ws1)
        svm.share_with(view_id, target_workspace_id="ws-2", auth=ctx_ws1)

        view = svm.get(view_id, auth=ctx_ws1)
        assert view.shared_with.count("ws-2") == 1
