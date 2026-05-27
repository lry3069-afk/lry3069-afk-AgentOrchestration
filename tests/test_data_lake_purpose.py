"""Tests for data lake purpose limitation enforcement.

Issue: #4584 — Enforce purpose limitation in data lake writes
"""

import pytest
from src.data_lake import (
    DataClass,
    Purpose,
    DestinationPolicy,
    DataClassificationRegistry,
    IngestionManifest,
    DataLakeIngestionPipeline,
    PurposeMismatchError,
    WriteDecision,
)


class TestDestinationPolicy:
    def test_permits_returns_true_when_class_and_purpose_allowed(self):
        policy = DestinationPolicy(
            destination="analytics-store",
            allowed_classes={DataClass.ANALYTICAL},
            allowed_purposes={Purpose.ANALYTICS},
            owner="data-team",
        )
        assert policy.permits(DataClass.ANALYTICAL, Purpose.ANALYTICS) is True

    def test_permits_returns_false_when_class_not_allowed(self):
        policy = DestinationPolicy(
            destination="analytical-store",
            allowed_classes={DataClass.ANALYTICAL},
            allowed_purposes={Purpose.ANALYTICS},
            owner="data-team",
        )
        assert policy.permits(DataClass.OPERATIONAL, Purpose.ANALYTICS) is False

    def test_permits_returns_false_when_purpose_not_allowed(self):
        policy = DestinationPolicy(
            destination="operational-store",
            allowed_classes={DataClass.OPERATIONAL},
            allowed_purposes={Purpose.OPERATIONAL_REPORTING},
            owner="ops-team",
        )
        assert policy.permits(DataClass.OPERATIONAL, Purpose.MACHINE_LEARNING) is False


class TestDataClassificationRegistry:
    def test_register_and_retrieve_policy(self):
        registry = DataClassificationRegistry()
        policy = DestinationPolicy(
            destination="analytics-store",
            allowed_classes={DataClass.ANALYTICAL},
            allowed_purposes={Purpose.ANALYTICS},
            owner="data-team",
        )
        registry.register_policy(policy)
        retrieved = registry.get_policy("analytics-store")
        assert retrieved is not None
        assert retrieved.destination == "analytics-store"
        assert retrieved.owner == "data-team"

    def test_get_policy_returns_none_for_unknown_destination(self):
        registry = DataClassificationRegistry()
        assert registry.get_policy("unknown") is None

    def test_evaluate_write_allowed_when_policy_permits(self):
        registry = DataClassificationRegistry()
        policy = DestinationPolicy(
            destination="analytics-store",
            allowed_classes={DataClass.ANALYTICAL},
            allowed_purposes={Purpose.ANALYTICS},
            owner="data-team",
        )
        registry.register_policy(policy)

        decision = registry.evaluate_write(
            destination="analytics-store",
            data_class=DataClass.ANALYTICAL,
            purpose=Purpose.ANALYTICS,
            owner="data-team",
        )
        assert decision.allowed is True

    def test_evaluate_write_rejected_when_no_policy_exists(self):
        registry = DataClassificationRegistry()
        decision = registry.evaluate_write(
            destination="unregistered-store",
            data_class=DataClass.ANALYTICAL,
            purpose=Purpose.ANALYTICS,
            owner="data-team",
        )
        assert decision.allowed is False
        assert "No policy registered" in decision.reason

    def test_evaluate_write_rejected_when_policy_denies(self):
        registry = DataClassificationRegistry()
        policy = DestinationPolicy(
            destination="restricted-store",
            allowed_classes={DataClass.SENSITIVE},
            allowed_purposes={Purpose.AUDITING},
            owner="security-team",
        )
        registry.register_policy(policy)

        decision = registry.evaluate_write(
            destination="restricted-store",
            data_class=DataClass.OPERATIONAL,
            purpose=Purpose.ANALYTICS,
            owner="data-team",
        )
        assert decision.allowed is False
        assert "does not allow" in decision.reason

    def test_audit_log_records_all_decisions(self):
        registry = DataClassificationRegistry()
        policy = DestinationPolicy(
            destination="analytics-store",
            allowed_classes={DataClass.ANALYTICAL},
            allowed_purposes={Purpose.ANALYTICS},
            owner="data-team",
        )
        registry.register_policy(policy)

        registry.evaluate_write(
            destination="analytics-store",
            data_class=DataClass.ANALYTICAL,
            purpose=Purpose.ANALYTICS,
            owner="data-team",
        )
        registry.evaluate_write(
            destination="unknown-store",
            data_class=DataClass.OPERATIONAL,
            purpose=Purpose.OPERATIONAL_REPORTING,
            owner="ops-team",
        )

        log = registry.get_audit_log()
        assert len(log) == 2
        assert log[0]["decision"] is True
        assert log[1]["decision"] is False


class TestIngestionManifest:
    def test_validate_passes_when_all_required_fields_present(self):
        manifest = IngestionManifest(
            destination="analytics-store",
            data_class=DataClass.ANALYTICAL,
            purpose=Purpose.ANALYTICS,
            owner="data-team",
        )
        manifest.validate()  # Should not raise

    def test_validate_raises_when_destination_missing(self):
        manifest = IngestionManifest(
            destination="",
            data_class=DataClass.ANALYTICAL,
            purpose=Purpose.ANALYTICS,
            owner="data-team",
        )
        with pytest.raises(PurposeMismatchError, match="destination"):
            manifest.validate()

    def test_validate_raises_when_data_class_missing(self):
        manifest = IngestionManifest(
            destination="analytics-store",
            data_class=DataClass.ANALYTICAL,
            purpose=Purpose.ANALYTICS,
            owner="",
        )
        with pytest.raises(PurposeMismatchError, match="owner"):
            manifest.validate()


class TestDataLakeIngestionPipeline:
    def test_register_destination_creates_policy(self):
        pipeline = DataLakeIngestionPipeline()
        pipeline.register_destination(
            destination="analytics-store",
            allowed_classes=[DataClass.ANALYTICAL],
            allowed_purposes=[Purpose.ANALYTICS],
            owner="data-team",
        )
        policy = pipeline.registry.get_policy("analytics-store")
        assert policy is not None
        assert DataClass.ANALYTICAL in policy.allowed_classes

    def test_write_succeeds_when_policy_allows(self):
        pipeline = DataLakeIngestionPipeline()
        pipeline.register_destination(
            destination="analytics-store",
            allowed_classes=[DataClass.ANALYTICAL],
            allowed_purposes=[Purpose.ANALYTICS],
            owner="data-team",
        )
        manifest = IngestionManifest(
            destination="analytics-store",
            data_class=DataClass.ANALYTICAL,
            purpose=Purpose.ANALYTICS,
            owner="data-team",
        )
        result = pipeline.write(manifest, data={"test": "value"})
        assert result is True

    def test_write_fails_when_destination_not_registered(self):
        pipeline = DataLakeIngestionPipeline()
        manifest = IngestionManifest(
            destination="unregistered-store",
            data_class=DataClass.OPERATIONAL,
            purpose=Purpose.OPERATIONAL_REPORTING,
            owner="ops-team",
        )
        with pytest.raises(PurposeMismatchError):
            pipeline.write(manifest, data={"test": "value"})

    def test_write_fails_when_policy_does_not_allow_data_class(self):
        pipeline = DataLakeIngestionPipeline()
        pipeline.register_destination(
            destination="restricted-store",
            allowed_classes=[DataClass.SENSITIVE],
            allowed_purposes=[Purpose.AUDITING],
            owner="security-team",
        )
        manifest = IngestionManifest(
            destination="restricted-store",
            data_class=DataClass.ANALYTICAL,
            purpose=Purpose.ANALYTICS,
            owner="data-team",
        )
        with pytest.raises(PurposeMismatchError):
            pipeline.write(manifest, data={"test": "value"})

    def test_write_fails_when_policy_does_not_allow_purpose(self):
        pipeline = DataLakeIngestionPipeline()
        pipeline.register_destination(
            destination="operational-store",
            allowed_classes=[DataClass.OPERATIONAL],
            allowed_purposes=[Purpose.OPERATIONAL_REPORTING],
            owner="ops-team",
        )
        manifest = IngestionManifest(
            destination="operational-store",
            data_class=DataClass.OPERATIONAL,
            purpose=Purpose.MACHINE_LEARNING,
            owner="ml-team",
        )
        with pytest.raises(PurposeMismatchError):
            pipeline.write(manifest, data={"test": "value"})

    def test_operational_data_rejected_to_analytical_store(self):
        """Issue #4584: Operational task events must not flow to analytical store."""
        pipeline = DataLakeIngestionPipeline()
        pipeline.register_destination(
            destination="analytical-store",
            allowed_classes=[DataClass.ANALYTICAL],
            allowed_purposes=[Purpose.ANALYTICS],
            owner="analytics-team",
        )
        manifest = IngestionManifest(
            destination="analytical-store",
            data_class=DataClass.OPERATIONAL,
            purpose=Purpose.OPERATIONAL_REPORTING,
            owner="ops-team",
        )
        with pytest.raises(PurposeMismatchError):
            pipeline.write(manifest, data={"task": "event"})

    def test_audit_report_shows_by_purpose_and_owner(self):
        pipeline = DataLakeIngestionPipeline()
        pipeline.register_destination(
            destination="analytics-store",
            allowed_classes=[DataClass.ANALYTICAL],
            allowed_purposes=[Purpose.ANALYTICS],
            owner="data-team",
        )
        pipeline.register_destination(
            destination="audit-store",
            allowed_classes=[DataClass.OPERATIONAL],
            allowed_purposes=[Purpose.AUDITING],
            owner="security-team",
        )

        manifest1 = IngestionManifest(
            destination="analytics-store",
            data_class=DataClass.ANALYTICAL,
            purpose=Purpose.ANALYTICS,
            owner="alice",
        )
        pipeline.write(manifest1, data={})

        manifest2 = IngestionManifest(
            destination="audit-store",
            data_class=DataClass.OPERATIONAL,
            purpose=Purpose.AUDITING,
            owner="bob",
        )
        pipeline.write(manifest2, data={})

        report = pipeline.get_audit_report()
        assert report["total_writes"] == 2
        assert report["allowed"] == 2
        assert report["by_purpose"]["analytics"] == 1
        assert report["by_purpose"]["auditing"] == 1
        assert report["by_owner"]["alice"] == 1
        assert report["by_owner"]["bob"] == 1

    def test_audit_report_tracks_rejected_writes(self):
        pipeline = DataLakeIngestionPipeline()
        pipeline.register_destination(
            destination="analytics-store",
            allowed_classes=[DataClass.ANALYTICAL],
            allowed_purposes=[Purpose.ANALYTICS],
            owner="data-team",
        )

        # Allowed write
        manifest_ok = IngestionManifest(
            destination="analytics-store",
            data_class=DataClass.ANALYTICAL,
            purpose=Purpose.ANALYTICS,
            owner="alice",
        )
        pipeline.write(manifest_ok, data={})

        # Rejected write - wrong destination
        manifest_bad = IngestionManifest(
            destination="unregistered-store",
            data_class=DataClass.OPERATIONAL,
            purpose=Purpose.OPERATIONAL_REPORTING,
            owner="bob",
        )
        with pytest.raises(PurposeMismatchError):
            pipeline.write(manifest_bad, data={})

        report = pipeline.get_audit_report()
        assert report["allowed"] == 1
        assert report["rejected"] == 1


# 2026-05-26T16:59:00 — Issue #4584: Purpose limitation enforcement tests