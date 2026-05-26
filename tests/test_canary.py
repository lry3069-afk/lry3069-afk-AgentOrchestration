"""Tests for the Canary Analyzer — queue backlog and lease renewal promotion logic."""

import pytest
from src.orchestrator.canary import (
    CanaryAnalyzer,
    CanaryMetrics,
    CanaryThresholds,
    QueueMetricsProvider,
    # Result classes
    CheckResult,
    # Emit helpers
    record_queue_depth,
    record_queue_growth_rate,
    record_processing_latency,
    record_processing_latency_p99,
    record_in_flight,
    record_lease_renewal,
    record_missing_heartbeat,
    record_http_request,
    # Provider
    QueueMetricsProvider,
)
from src.common.metrics import metrics


class TestCanaryThresholds:
    """Threshold dataclass — verify overridden() cloning."""

    def test_override_queue_depth(self):
        t = CanaryThresholds(queue_depth_max=200)
        assert t.queue_depth_max == 200
        assert t.queue_depth_promote == 20  # unchanged

    def test_override_processing_latency(self):
        t = CanaryThresholds(processing_latency_max=60.0)
        assert t.processing_latency_max == 60.0

    def test_override_lease_failure_rate(self):
        t = CanaryThresholds(lease_renewal_failure_rate_max=0.05)
        assert t.lease_renewal_failure_rate_max == 0.05


class TestCanaryMetrics:
    """CanaryMetrics dataclass."""

    def test_age_seconds_positive(self):
        m = CanaryMetrics()
        assert m.age_seconds >= 0

    def test_default_values(self):
        m = CanaryMetrics()
        assert m.queue_depth == 0
        assert m.lease_renewal_failure_rate == 0.0
        assert m.http_success_rate == 1.0


class TestCheckResult:
    """CheckResult dataclass."""

    def test_passed_result(self):
        r = CheckResult(passed=True, name="queue_depth", message="OK")
        assert r.passed is True
        assert r.detail is None

    def test_failed_result_with_detail(self):
        r = CheckResult(
            passed=False,
            name="lease_renewal_failure_rate",
            message="Lease renewal failure rate 15.0% exceeds 10.0%",
            detail={"failure_rate": 0.15, "threshold": 0.10},
        )
        assert r.passed is False
        assert r.detail["failure_rate"] == 0.15


class TestCanaryAnalyzerQueueDepth:
    """Acceptance #1: Canary promotion fails when backlog exceeds thresholds."""

    def test_rollback_when_hard_max_exceeded(self):
        analyzer = CanaryAnalyzer(thresholds=CanaryThresholds(queue_depth_max=50))
        m = CanaryMetrics(queue_depth=60)
        decision, results, score = analyzer.evaluate(m)
        assert decision == CanaryAnalyzer.ROLLBACK

    def test_hold_when_promote_threshold_exceeded_below_max(self):
        # depth above promote threshold but below hard max → HOLD
        analyzer = CanaryAnalyzer(thresholds=CanaryThresholds(
            queue_depth_max=100, queue_depth_promote=20))
        m = CanaryMetrics(queue_depth=50)
        decision, results, score = analyzer.evaluate(m)
        assert decision == CanaryAnalyzer.HOLD

    def test_promote_when_below_promote_threshold(self):
        analyzer = CanaryAnalyzer(thresholds=CanaryThresholds(
            queue_depth_max=100, queue_depth_promote=20))
        m = CanaryMetrics(queue_depth=10)
        decision, results, score = analyzer.evaluate(m)
        assert decision == CanaryAnalyzer.PROMOTE
        assert any(r.name == "queue_depth" and r.passed for r in results)

    def test_queue_depth_check_result_contains_detail(self):
        analyzer = CanaryAnalyzer(thresholds=CanaryThresholds(queue_depth_max=10))
        m = CanaryMetrics(queue_depth=25)
        result = analyzer.check_queue_depth(m)
        assert result.passed is False
        assert result.detail["queue_depth"] == 25
        assert result.detail["threshold"] == 10


class TestCanaryAnalyzerQueueGrowth:
    """Queue growth rate detection."""

    def test_hold_on_excessive_growth_rate(self):
        # queue_growth_rate is a non-hard metric → degrades score, triggers HOLD
        analyzer = CanaryAnalyzer(thresholds=CanaryThresholds(queue_growth_rate_max=2.0))
        m = CanaryMetrics(queue_growth_rate=5.0)
        decision, results, score = analyzer.evaluate(m)
        assert decision == CanaryAnalyzer.HOLD

    def test_promote_within_growth_rate(self):
        analyzer = CanaryAnalyzer(thresholds=CanaryThresholds(queue_growth_rate_max=2.0))
        m = CanaryMetrics(queue_growth_rate=0.5, queue_depth=5,
                          processing_latency_avg=0.1, processing_latency_p99=0.5,
                          http_success_rate=1.0, http_latency_p99=0.1)
        decision, results, score = analyzer.evaluate(m)
        assert decision == CanaryAnalyzer.PROMOTE


class TestCanaryAnalyzerProcessingLatency:
    """Acceptance #1: Canary promotion fails when processing latency exceeds thresholds."""

    def test_rollback_on_avg_latency_exceeding_max(self):
        analyzer = CanaryAnalyzer(thresholds=CanaryThresholds(
            processing_latency_max=10.0, processing_latency_p99_max=10.0))
        m = CanaryMetrics(processing_latency_avg=15.0, processing_latency_p99=5.0)
        decision, results, score = analyzer.evaluate(m)
        assert decision == CanaryAnalyzer.ROLLBACK

    def test_rollback_on_p99_latency_exceeding_max(self):
        analyzer = CanaryAnalyzer(thresholds=CanaryThresholds(
            processing_latency_max=30.0, processing_latency_p99_max=5.0))
        m = CanaryMetrics(processing_latency_avg=1.0, processing_latency_p99=8.0)
        decision, results, score = analyzer.evaluate(m)
        assert decision == CanaryAnalyzer.ROLLBACK

    def test_promote_with_healthy_latency(self):
        analyzer = CanaryAnalyzer(thresholds=CanaryThresholds(
            processing_latency_max=30.0, processing_latency_p99_max=10.0,
            queue_depth_promote=100))
        m = CanaryMetrics(
            processing_latency_avg=5.0, processing_latency_p99=8.0,
            queue_depth=10, http_success_rate=0.99, http_latency_p99=0.5)
        decision, results, score = analyzer.evaluate(m)
        assert decision == CanaryAnalyzer.PROMOTE

    def test_processing_latency_check_detail(self):
        analyzer = CanaryAnalyzer(thresholds=CanaryThresholds(
            processing_latency_max=5.0, processing_latency_p99_max=3.0))
        result = analyzer.check_processing_latency(
            CanaryMetrics(processing_latency_avg=10.0, processing_latency_p99=4.0))
        assert result.passed is False
        assert "avg=10.0s > max=5.0s" in result.message


class TestCanaryAnalyzerLeaseRenewal:
    """Acceptance #2: Worker lease renewal failures contribute to rollback decisions."""

    def test_rollback_on_lease_renewal_failure_rate_exceeded(self):
        analyzer = CanaryAnalyzer(thresholds=CanaryThresholds(
            lease_renewal_failure_rate_max=0.10))
        m = CanaryMetrics(
            lease_renewal_failures=15, lease_renewal_total=100,
            lease_renewal_failure_rate=0.15)
        decision, results, score = analyzer.evaluate(m)
        assert decision == CanaryAnalyzer.ROLLBACK

    def test_rollback_on_missing_heartbeats(self):
        # lease_heartbeat_missing IS a hard-fail metric → ROLLBACK
        analyzer = CanaryAnalyzer(thresholds=CanaryThresholds(
            lease_renewal_heartbeat_missing_max=3))
        m = CanaryMetrics(missing_heartbeats=4)
        decision, results, score = analyzer.evaluate(m)
        assert decision == CanaryAnalyzer.ROLLBACK

    def test_promote_with_healthy_lease_metrics(self):
        analyzer = CanaryAnalyzer(thresholds=CanaryThresholds(
            lease_renewal_failure_rate_max=0.10,
            lease_renewal_heartbeat_missing_max=3,
            queue_depth_promote=100, processing_latency_max=30.0,
            processing_latency_p99_max=10.0, http_success_rate_min=0.95,
            http_latency_p99_max=2.0))
        m = CanaryMetrics(
            lease_renewal_failures=1, lease_renewal_total=100,
            lease_renewal_failure_rate=0.01,
            missing_heartbeats=0,
            queue_depth=10, processing_latency_avg=1.0, processing_latency_p99=3.0,
            http_success_rate=0.99, http_latency_p99=0.5)
        decision, results, score = analyzer.evaluate(m)
        assert decision == CanaryAnalyzer.PROMOTE

    def test_lease_check_detail_contains_failure_rate(self):
        analyzer = CanaryAnalyzer(thresholds=CanaryThresholds(
            lease_renewal_failure_rate_max=0.10))
        result = analyzer.check_lease_renewal(
            CanaryMetrics(lease_renewal_failures=20, lease_renewal_total=100,
                          lease_renewal_failure_rate=0.20))
        assert result.passed is False
        assert result.detail["failure_rate"] == 0.20


class TestCanaryAnalyzerHTTPHealth:
    """Existing HTTP health checks still pass / fail correctly."""

    def test_promote_with_good_http_health(self):
        analyzer = CanaryAnalyzer(thresholds=CanaryThresholds(
            http_success_rate_min=0.95, http_latency_p99_max=2.0,
            queue_depth_promote=100, processing_latency_max=30.0,
            processing_latency_p99_max=10.0, lease_renewal_failure_rate_max=0.10,
            lease_renewal_heartbeat_missing_max=3))
        m = CanaryMetrics(
            http_success_rate=0.98, http_latency_p99=0.8,
            queue_depth=10, processing_latency_avg=1.0, processing_latency_p99=3.0,
            lease_renewal_failure_rate=0.0, missing_heartbeats=0)
        decision, results, score = analyzer.evaluate(m)
        assert decision == CanaryAnalyzer.PROMOTE

    def test_rollback_on_low_http_success_rate(self):
        analyzer = CanaryAnalyzer(thresholds=CanaryThresholds(
            http_success_rate_min=0.95))
        m = CanaryMetrics(http_success_rate=0.80)
        decision, results, score = analyzer.evaluate(m)
        assert decision == CanaryAnalyzer.HOLD  # not a hard rollback metric


class TestCanaryAnalyzerCompositeScore:
    """Score computation and threshold interaction."""

    def test_score_zero_when_all_checks_fail(self):
        analyzer = CanaryAnalyzer(thresholds=CanaryThresholds(
            queue_depth_max=10, queue_depth_promote=5,
            processing_latency_max=5.0, processing_latency_p99_max=3.0,
            lease_renewal_failure_rate_max=0.05,
            http_success_rate_min=0.99, http_latency_p99_max=0.5,
            promotion_score_min=0.70))
        m = CanaryMetrics(
            queue_depth=200, queue_growth_rate=10.0,
            processing_latency_avg=50.0, processing_latency_p99=30.0,
            lease_renewal_failure_rate=0.30, missing_heartbeats=5,
            http_success_rate=0.50, http_latency_p99=10.0)
        decision, results, score = analyzer.evaluate(m)
        assert score == 0.0
        assert decision == CanaryAnalyzer.ROLLBACK

    def test_score_perfect_when_all_checks_pass(self):
        analyzer = CanaryAnalyzer()
        m = CanaryMetrics(
            queue_depth=5, queue_growth_rate=0.1,
            processing_latency_avg=0.5, processing_latency_p99=1.0,
            lease_renewal_failure_rate=0.0, missing_heartbeats=0,
            http_success_rate=1.0, http_latency_p99=0.1)
        decision, results, score = analyzer.evaluate(m)
        assert score == 1.0
        assert decision == CanaryAnalyzer.PROMOTE

    def test_hard_fail_takes_precedence_over_hold(self):
        """Hard-fail metrics (queue_depth_max, processing_latency, lease) → ROLLBACK."""
        analyzer = CanaryAnalyzer(thresholds=CanaryThresholds(
            queue_depth_max=10, processing_latency_max=5.0,
            lease_renewal_failure_rate_max=0.05,
            queue_depth_promote=5, processing_latency_p99_max=3.0,
            http_success_rate_min=0.95, http_latency_p99_max=2.0))
        m = CanaryMetrics(
            queue_depth=200,  # hard fail
            processing_latency_avg=1.0, processing_latency_p99=2.0,
            lease_renewal_failure_rate=0.0, missing_heartbeats=0,
            http_success_rate=1.0, http_latency_p99=0.1)
        decision, results, score = analyzer.evaluate(m)
        assert decision == CanaryAnalyzer.ROLLBACK


class TestQueueMetricsProvider:
    """QueueMetricsProvider reads from MetricsCollector gauges and counters."""

    def test_provider_reads_queue_depth(self):
        # Record a value and verify the provider picks it up
        metrics._gauges.clear()
        metrics._counters.clear()
        metrics._histograms.clear()
        record_queue_depth(42)
        provider = QueueMetricsProvider()
        m = provider.snapshot()
        assert m.queue_depth == 42

    def test_provider_reads_growth_rate(self):
        metrics._gauges.clear()
        metrics._counters.clear()
        metrics._histograms.clear()
        record_queue_growth_rate(1.5)
        provider = QueueMetricsProvider()
        m = provider.snapshot()
        assert m.queue_growth_rate == 1.5

    def test_provider_reads_lease_renewal(self):
        metrics._gauges.clear()
        metrics._counters.clear()
        metrics._histograms.clear()
        record_lease_renewal(success=True)
        record_lease_renewal(success=False)
        record_lease_renewal(success=False)
        provider = QueueMetricsProvider()
        m = provider.snapshot()
        assert m.lease_renewal_total == 3
        assert m.lease_renewal_failures == 2

    def test_provider_reads_processing_latency(self):
        metrics._gauges.clear()
        metrics._counters.clear()
        metrics._histograms.clear()
        record_processing_latency(2.5)
        record_processing_latency(3.5)
        record_processing_latency_p99(4.1)
        provider = QueueMetricsProvider()
        m = provider.snapshot()
        # avg of 2 samples
        assert m.processing_latency_avg == 3.0
        assert m.processing_latency_p99 == 4.1

    def test_provider_reads_http_metrics(self):
        metrics._gauges.clear()
        metrics._counters.clear()
        metrics._histograms.clear()
        for _ in range(100):
            record_http_request(error=False, latency_seconds=0.5)
        for _ in range(5):
            record_http_request(error=True, latency_seconds=1.0)
        provider = QueueMetricsProvider()
        m = provider.snapshot()
        assert m.http_requests_total == 105
        assert m.http_errors_total == 5
        assert abs(m.http_success_rate - 100 / 105 - 0.0001) < 0.01


class TestEmitHelpers:
    """Verify each emit helper writes to the correct metric key."""

    def test_record_in_flight(self):
        metrics._gauges.clear()
        record_in_flight(7)
        assert metrics._gauges.get(QueueMetricsProvider.KEY_IN_FLIGHT) == 7.0

    def test_record_missing_heartbeat(self):
        metrics._counters.clear()
        record_missing_heartbeat()
        assert metrics._counters.get(QueueMetricsProvider.KEY_LEASE_MISSED_HB) == 1

    def test_record_http_request_counts(self):
        metrics._counters.clear()
        metrics._histograms.clear()
        record_http_request(error=False, latency_seconds=0.3)
        assert metrics._counters.get(QueueMetricsProvider.KEY_HTTP_TOTAL) == 1
        assert metrics._counters.get(QueueMetricsProvider.KEY_HTTP_ERRORS, 0) == 0


# 2026-05-26 implementation: queue backlog-aware canary promotion (Issue #4587)
