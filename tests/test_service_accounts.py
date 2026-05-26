import pytest
from src.identity.service_accounts import (
    ServiceAccountManager,
    ServiceAccountStatus,
    DuplicateExternalIDError,
)


class TestServiceAccountManager:
    def setup_method(self):
        self.manager = ServiceAccountManager()
        self.org_id = "org-main-001"

    def test_create_account_succeeds(self):
        account_id = self.manager.create_account(
            self.org_id, "ext-id-001", "CI Pipeline Bot"
        )
        account = self.manager.get_account(account_id)
        assert account is not None
        assert account["external_id"] == "ext-id-001"
        assert account["status"] == ServiceAccountStatus.ACTIVE.value

    def test_duplicate_active_external_id_rejected(self):
        self.manager.create_account(self.org_id, "ext-id-001", "Bot A")
        with pytest.raises(DuplicateExternalIDError) as exc:
            self.manager.create_account(self.org_id, "ext-id-001", "Bot B")
        assert "ext-id-001" in str(exc.value)
        assert self.org_id in str(exc.value)

    def test_same_external_id_allowed_in_different_org(self):
        self.manager.create_account("org-alpha", "shared-id", "Bot A")
        # Same external ID in different org should succeed
        account_id = self.manager.create_account("org-beta", "shared-id", "Bot B")
        account = self.manager.get_account(account_id)
        assert account["external_id"] == "shared-id"

    def test_update_external_id_rejects_duplicate(self):
        self.manager.create_account(self.org_id, "ext-001", "Bot A")
        account_id = self.manager.create_account(self.org_id, "ext-002", "Bot B")
        with pytest.raises(DuplicateExternalIDError):
            self.manager.update_account(account_id, external_id="ext-001")

    def test_disable_and_restore_account(self):
        account_id = self.manager.create_account(self.org_id, "ext-id-001", "Bot A")
        assert self.manager.disable_account(account_id)

        account = self.manager.get_account(account_id)
        assert account["status"] == ServiceAccountStatus.DISABLED.value

        # Restore
        assert self.manager.restore_account(account_id)
        account = self.manager.get_account(account_id)
        assert account["status"] == ServiceAccountStatus.ACTIVE.value

    def test_restore_fails_if_external_id_taken(self):
        account_a = self.manager.create_account(self.org_id, "ext-001", "Bot A")
        self.manager.disable_account(account_a)
        # Another account takes the external ID
        self.manager.create_account(self.org_id, "ext-001", "Bot C")
        with pytest.raises(DuplicateExternalIDError):
            self.manager.restore_account(account_a)

    def test_delete_frees_external_id(self):
        self.manager.create_account(self.org_id, "ext-id-001", "Bot A")
        account_id = self.manager.create_account(self.org_id, "ext-id-002", "Bot B")
        self.manager.delete_account(account_id)
        # Now ext-id-002 should be free to reuse
        new_id = self.manager.create_account(self.org_id, "ext-id-002", "Bot C")
        assert new_id is not None

    def test_get_by_external_id(self):
        self.manager.create_account(self.org_id, "ext-find-me", "Find Bot")
        account = self.manager.get_by_external_id(self.org_id, "ext-find-me")
        assert account is not None
        assert account["external_id"] == "ext-find-me"

    def test_get_by_external_id_returns_none_for_missing(self):
        account = self.manager.get_by_external_id(self.org_id, "nonexistent")
        assert account is None

    def test_list_accounts_by_org(self):
        self.manager.create_account(self.org_id, "ext-001", "Bot A")
        self.manager.create_account(self.org_id, "ext-002", "Bot B")
        self.manager.create_account("other-org", "ext-003", "Bot C")
        accounts = self.manager.list_accounts(self.org_id)
        assert len(accounts) == 2
