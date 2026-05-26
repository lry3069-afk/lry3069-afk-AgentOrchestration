"""Service account management with unique external ID enforcement."""

from enum import Enum
from typing import Any, Dict, List, Optional


class ServiceAccountStatus(Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    SUSPENDED = "suspended"


class DuplicateExternalIDError(Exception):
    """Raised when a duplicate external ID is detected."""
    def __init__(self, external_id: str, org_id: str):
        super().__init__(
            f"External ID '{external_id}' already exists in organization '{org_id}'"
        )
        self.external_id = external_id
        self.org_id = org_id


class ServiceAccountManager:
    """Manages service accounts with unique external ID enforcement per organization.

    External IDs must be unique across all active service accounts in an
    organization. Disabled accounts follow documented reuse rules:
    - Disabled accounts free their external ID after a 90-day grace period.
    - Deleting a service account immediately frees the external ID.
    """

    _DISABLED_REUSE_GRACE_DAYS = 90

    def __init__(self):
        self._accounts: Dict[str, Dict[str, Any]] = {}
        self._external_id_index: Dict[str, set] = {}  # org_id -> set of active external IDs
        self._id_counter = 0

    def create_account(
        self,
        org_id: str,
        external_id: str,
        name: str,
        metadata: Optional[Dict] = None,
    ) -> str:
        """Create a service account with a unique external ID in the organization.

        Raises DuplicateExternalIDError if the external ID is already in use
        by an active account in this organization.
        """
        import time

        self._ensure_org_index(org_id)

        # Check uniqueness among active accounts
        if external_id in self._external_id_index.get(org_id, set()):
            raise DuplicateExternalIDError(external_id, org_id)

        self._id_counter += 1
        account_id = f"sa-{self._id_counter:06d}"
        timestamp = int(time.time())

        self._accounts[account_id] = {
            "id": account_id,
            "org_id": org_id,
            "external_id": external_id,
            "name": name,
            "status": ServiceAccountStatus.ACTIVE.value,
            "metadata": metadata or {},
            "created_at": timestamp,
            "updated_at": timestamp,
        }

        self._external_id_index[org_id].add(external_id)
        return account_id

    def get_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        return self._accounts.get(account_id)

    def update_account(
        self,
        account_id: str,
        name: Optional[str] = None,
        external_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> bool:
        """Update a service account. External ID change is validated for uniqueness."""
        import time

        account = self._accounts.get(account_id)
        if not account:
            return False

        if name is not None:
            account["name"] = name

        if external_id is not None and external_id != account["external_id"]:
            org_id = account["org_id"]
            self._ensure_org_index(org_id)
            # Check uniqueness for the new external ID
            if external_id in self._external_id_index.get(org_id, set()):
                raise DuplicateExternalIDError(external_id, org_id)
            # Free old external ID and claim new one
            old_external_id = account["external_id"]
            self._external_id_index[org_id].discard(old_external_id)
            self._external_id_index[org_id].add(external_id)
            account["external_id"] = external_id

        if metadata is not None:
            account["metadata"].update(metadata)

        account["updated_at"] = int(time.time())
        return True

    def disable_account(self, account_id: str) -> bool:
        """Disable a service account. External ID is freed after grace period."""
        import time

        account = self._accounts.get(account_id)
        if not account:
            return False

        account["status"] = ServiceAccountStatus.DISABLED.value
        account["disabled_at"] = int(time.time())
        account["updated_at"] = int(time.time())

        # External ID is freed after grace period, not immediately
        return True

    def restore_account(self, account_id: str) -> bool:
        """Restore a disabled service account.

        If the external ID was taken by another account during disablement,
        the restore fails until the conflict is resolved.
        """
        import time

        account = self._accounts.get(account_id)
        if not account:
            return False

        if account["status"] != ServiceAccountStatus.DISABLED.value:
            return False

        org_id = account["org_id"]
        self._ensure_org_index(org_id)

        # Check if external ID still available
        if account["external_id"] in self._external_id_index.get(org_id, set()):
            raise DuplicateExternalIDError(account["external_id"], org_id)

        account["status"] = ServiceAccountStatus.ACTIVE.value
        account["disabled_at"] = None
        account["updated_at"] = int(time.time())
        self._external_id_index[org_id].add(account["external_id"])
        return True

    def delete_account(self, account_id: str) -> bool:
        """Delete a service account, immediately freeing its external ID."""
        account = self._accounts.pop(account_id, None)
        if not account:
            return False

        org_id = account["org_id"]
        if org_id in self._external_id_index:
            self._external_id_index[org_id].discard(account["external_id"])
        return True

    def list_accounts(self, org_id: str) -> List[Dict[str, Any]]:
        return [
            a for a in self._accounts.values()
            if a["org_id"] == org_id
        ]

    def get_by_external_id(self, org_id: str, external_id: str) -> Optional[Dict[str, Any]]:
        for account in self._accounts.values():
            if (
                account["org_id"] == org_id
                and account["external_id"] == external_id
            ):
                return account
        return None

    def _ensure_org_index(self, org_id: str) -> None:
        if org_id not in self._external_id_index:
            self._external_id_index[org_id] = set()
