"""Data Lake Pipeline — Purpose limitation and classification enforcement.

Issue: #4584 — Enforce purpose limitation in data lake writes
"""

import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class DataClass(str, Enum):
    """Data classification levels for the data lake."""
    OPERATIONAL = "operational"          # Internal operational events
    ANALYTICAL = "analytical"            # Analytics and reporting
    SENSITIVE = "sensitive"              # Restricted / confidential
    PUBLIC = "public"                    # Publicly releasable


class Purpose(str, Enum):
    """Declared purpose for data ingestion."""
    OPERATIONAL_REPORTING = "operational_reporting"
    ANALYTICS = "analytics"
    MACHINE_LEARNING = "machine_learning"
    AUDITING = "auditing"
    CROSS_SYSTEM_INTEGRATION = "cross_system_integration"


@dataclass
class DestinationPolicy:
    """Defines which data classes are allowed for a given destination."""
    destination: str
    allowed_classes: Set[DataClass] = field(default_factory=set)
    allowed_purposes: Set[Purpose] = field(default_factory=set)
    owner: str = ""

    def permits(self, data_class: DataClass, purpose: Purpose) -> bool:
        return data_class in self.allowed_classes and purpose in self.allowed_purposes


class DataClassificationRegistry:
    """Central registry of destination policies.

    Enforces that data lake writes respect purpose limitation and
    data classification constraints.
    """

    def __init__(self):
        self._policies: Dict[str, DestinationPolicy] = {}
        self._audit_log: List[Dict[str, Any]] = []

    def register_policy(self, policy: DestinationPolicy) -> None:
        self._policies[policy.destination] = policy
        logger.info(
            "policy_registered",
            extra={
                "destination": policy.destination,
                "allowed_classes": [c.value for c in policy.allowed_classes],
                "allowed_purposes": [p.value for p in policy.allowed_purposes],
                "owner": policy.owner,
            },
        )

    def get_policy(self, destination: str) -> Optional[DestinationPolicy]:
        return self._policies.get(destination)

    def evaluate_write(
        self,
        destination: str,
        data_class: DataClass,
        purpose: Purpose,
        owner: str,
    ) -> "WriteDecision":
        """Evaluate whether a data write is permitted.

        Returns a WriteDecision indicating whether the write is allowed,
        and logs the decision for audit purposes.
        """
        policy = self._policies.get(destination)
        if policy is None:
            decision = WriteDecision(
                allowed=False,
                reason=f"No policy registered for destination '{destination}'",
                destination=destination,
                data_class=data_class,
                purpose=purpose,
                owner=owner,
            )
        elif not policy.permits(data_class, purpose):
            decision = WriteDecision(
                allowed=False,
                reason=(
                    f"Policy for '{destination}' does not allow "
                    f"data class '{data_class.value}' with purpose '{purpose.value}'"
                ),
                destination=destination,
                data_class=data_class,
                purpose=purpose,
                owner=owner,
            )
        else:
            decision = WriteDecision(
                allowed=True,
                reason="Policy check passed",
                destination=destination,
                data_class=data_class,
                purpose=purpose,
                owner=owner,
            )

        self._audit_log.append({
            "destination": destination,
            "data_class": data_class.value,
            "purpose": purpose.value,
            "owner": owner,
            "decision": decision.allowed,
            "reason": decision.reason,
        })
        return decision

    def get_audit_log(self) -> List[Dict[str, Any]]:
        return list(self._audit_log)

    def clear_audit_log(self) -> None:
        self._audit_log.clear()


@dataclass
class WriteDecision:
    allowed: bool
    reason: str
    destination: str
    data_class: DataClass
    purpose: Purpose
    owner: str


class PurposeMismatchError(ValueError):
    """Raised when an ingestion write violates purpose limitations."""

    def __init__(self, message: str, decision: Optional[WriteDecision] = None):
        super().__init__(message)
        self.decision = decision


@dataclass
class IngestionManifest:
    """Manifest for a data lake ingestion write.

    Captures all required metadata for purpose limitation enforcement.
    """
    destination: str
    data_class: DataClass
    purpose: Purpose
    owner: str
    schema_validated: bool = False

    def validate(self) -> None:
        """Validate required fields are present and non-empty."""
        if not self.destination:
            raise PurposeMismatchError("Manifest missing required field: destination")
        if not self.data_class:
            raise PurposeMismatchError("Manifest missing required field: data_class")
        if not self.purpose:
            raise PurposeMismatchError("Manifest missing required field: purpose")
        if not self.owner:
            raise PurposeMismatchError("Manifest missing required field: owner")


class DataLakeIngestionPipeline:
    """Ingestion pipeline that enforces purpose limitation.

    All writes to the data lake must pass purpose metadata validation
    and data classification policy checks before being processed.
    """

    def __init__(self, registry: Optional[DataClassificationRegistry] = None):
        self._registry = registry or DataClassificationRegistry()
        self._audit_counts: Dict[str, int] = {
            "allowed": 0,
            "rejected": 0,
        }

    @property
    def registry(self) -> DataClassificationRegistry:
        return self._registry

    def register_destination(
        self,
        destination: str,
        allowed_classes: List[DataClass],
        allowed_purposes: List[Purpose],
        owner: str = "",
    ) -> None:
        policy = DestinationPolicy(
            destination=destination,
            allowed_classes=set(allowed_classes),
            allowed_purposes=set(allowed_purposes),
            owner=owner,
        )
        self._registry.register_policy(policy)

    def write(
        self,
        manifest: IngestionManifest,
        data: Any,
    ) -> bool:
        """Ingest data into the data lake with purpose enforcement.

        Raises:
            PurposeMismatchError: If the write is not permitted by policy.
        """
        manifest.validate()

        decision = self._registry.evaluate_write(
            destination=manifest.destination,
            data_class=manifest.data_class,
            purpose=manifest.purpose,
            owner=manifest.owner,
        )

        if not decision.allowed:
            logger.warning(
                "ingestion_rejected",
                extra={
                    "destination": manifest.destination,
                    "data_class": manifest.data_class.value,
                    "purpose": manifest.purpose.value,
                    "owner": manifest.owner,
                    "reason": decision.reason,
                },
            )
            self._audit_counts["rejected"] += 1
            raise PurposeMismatchError(decision.reason, decision)

        logger.info(
            "ingestion_allowed",
            extra={
                "destination": manifest.destination,
                "data_class": manifest.data_class.value,
                "purpose": manifest.purpose.value,
                "owner": manifest.owner,
            },
        )
        self._audit_counts["allowed"] += 1
        return True

    def get_audit_report(self) -> Dict[str, Any]:
        """Return audit report summarizing data lake writes by purpose and owner."""
        log = self._registry.get_audit_log()
        by_purpose: Dict[str, int] = {}
        by_owner: Dict[str, int] = {}
        by_class: Dict[str, int] = {}
        rejected_total = 0

        for entry in log:
            purpose_key = entry["purpose"]
            owner_key = entry["owner"]
            class_key = entry["data_class"]
            by_purpose[purpose_key] = by_purpose.get(purpose_key, 0) + 1
            by_owner[owner_key] = by_owner.get(owner_key, 0) + 1
            by_class[class_key] = by_class.get(class_key, 0) + 1
            if not entry["decision"]:
                rejected_total += 1

        return {
            "total_writes": len(log),
            "allowed": self._audit_counts["allowed"],
            "rejected": self._audit_counts["rejected"],
            "by_purpose": by_purpose,
            "by_owner": by_owner,
            "by_data_class": by_class,
        }

    def get_audit_log(self) -> List[Dict[str, Any]]:
        return self._registry.get_audit_log()


# 2026-05-26T16:59:00 — Issue #4584: Purpose limitation enforcement in data lake writes