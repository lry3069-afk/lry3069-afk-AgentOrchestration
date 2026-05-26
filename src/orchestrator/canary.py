"""Canary Deployment Analysis — Queue-aware promotion decision engine.

Evaluates canary health using HTTP health checks, error rates, queue depth,
processing latency, and worker lease renewal metrics. Fails promotion when
any threshold is breached, ensuring background task throughput is maintained.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from src.common.metrics import metrics

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Threshold configuration
# ---------------------------------------------------------------------------

@dataclass
class CanaryThresholds:
    """Tunable thresholds for canary promotion decisions."""

    # Queue / backlog
    queue_depth_max: int = 100          # max items in queue before rollback
    queue_depth_promote: int = 20        # must be below this to promote
    queue_growth_rate_max: float = 2.0   # items/sec growth rate threshold

    # Processing latency (seconds)
    processing_latency_max: float = 30.0   # hard rollback ceiling
    processing_latency_p99_max: float = 10.0  # p99 must stay below this

    # Lease renewal
    lease_renewal_failure_rate_max: float = 0.10   # 10% failure rate = rollback
    lease_renewal_heartbeat_missing_max: int = 3   # missed heartbeats before alert

    # HTTP health (existing criteria — preserved)
    http_success_rate_min: float = 0.95
    http_error_rate_max: float = 0.05
    http_latency_p99_max: float = 2.0

    # Composite score
    promotion_score_min: float = 0.70   # overall score 0-1, must exceed to promote

    def overridden(self, **kwargs) -> "CanaryThresholds":
        """Return a copy with overrides applied."""
        import copy
        cfg = copy.copy(self)
        for k, v in kwargs.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg


# ---------------------------------------------------------------------------
# Metric snapshot
# ---------------------------------------------------------------------------

@dataclass
class CanaryMetrics:
    """Point-in-time snapshot of canary-relevant metrics."""

    # Queue state
    queue_depth: int = 0
    queue_growth_rate: float = 0.0      # items/second
    scheduler_in_flight: int = 0

    # Processing latency (seconds)
    processing_latency_avg: float = 0.0
    processing_latency_p99: float = 0.0

    # Lease / worker health
    lease_renewal_failures: int = 0
    lease_renewal_total: int = 0
    lease_renewal_failure_rate: float = 0.0
    missing_heartbeats: int = 0

    # HTTP health
    http_requests_total: int = 0
    http_errors_total: int = 0
    http_success_rate: float = 1.0
    http_latency_p99: float = 0.0

    # Timestamp
    sampled_at: float = field(default_factory=time.time)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.sampled_at


# ---------------------------------------------------------------------------
# Queue metrics helper (reads from scheduler)
# ---------------------------------------------------------------------------

class QueueMetricsProvider:
    """Reads queue / backlog metrics from the scheduler / metrics collector.

    Plugs into the existing MetricsCollector so all gauges are co-located.
    """

    # Well-known metric keys
    KEY_QUEUE_DEPTH         = "scheduler.queue.depth"
    KEY_QUEUE_GROWTH_RATE   = "scheduler.queue.growth_rate"
    KEY_IN_FLIGHT           = "scheduler.in_flight"
    KEY_PROCESSING_LAT_AVG  = "scheduler.processing.latency.avg"
    KEY_PROCESSING_LAT_P99   = "scheduler.processing.latency.p99"
    KEY_LEASE_FAILURES      = "worker.lease.renewal.failures"
    KEY_LEASE_TOTAL         = "worker.lease.renewal.total"
    KEY_LEASE_MISSED_HB     = "worker.lease.heartbeat.missing"
    KEY_HTTP_TOTAL           = "http.requests.total"
    KEY_HTTP_ERRORS          = "http.errors.total"
    KEY_HTTP_LAT_P99         = "http.latency.p99"

    def snapshot(self) -> CanaryMetrics:
        snap = metrics.snapshot()

        gauges = snap.get("gauges", {})
        counters = snap.get("counters", {})
        histograms = snap.get("histograms", {})

        m = CanaryMetrics()

        m.queue_depth        = int(gauges.get(self.KEY_QUEUE_DEPTH, 0))
        m.queue_growth_rate  = float(gauges.get(self.KEY_QUEUE_GROWTH_RATE, 0.0))
        m.scheduler_in_flight= int(gauges.get(self.KEY_IN_FLIGHT, 0))

        m.processing_latency_avg = float(
            histograms.get(self.KEY_PROCESSING_LAT_AVG, {}).get("avg", 0.0)
        )
        m.processing_latency_p99 = float(
            gauges.get(self.KEY_PROCESSING_LAT_P99, 0.0)
        )

        m.lease_renewal_failures = counters.get(self.KEY_LEASE_FAILURES, 0)
        m.lease_renewal_total    = counters.get(self.KEY_LEASE_TOTAL, 0)
        m.missing_heartbeats     = counters.get(self.KEY_LEASE_MISSED_HB, 0)

        total = counters.get(self.KEY_HTTP_TOTAL, 0)
        errors = counters.get(self.KEY_HTTP_ERRORS, 0)
        m.http_requests_total = total
        m.http_errors_total   = errors
        m.http_success_rate   = (total - errors) / total if total > 0 else 1.0
        m.http_latency_p99    = float(gauges.get(self.KEY_HTTP_LAT_P99, 0.0))

        m.sampled_at = time.time()
        return m


# ---------------------------------------------------------------------------
# Individual check results
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    passed: bool
    name: str
    message: str
    detail: Optional[Dict] = None


# ---------------------------------------------------------------------------
# Main analyzer
# ---------------------------------------------------------------------------

class CanaryAnalyzer:
    """Evaluates canary promotion using queue depth, latency, and lease metrics.

    Call ``evaluate()`` after collecting metrics for the canary window. The
    result tells you whether to promote, rollback, or continue observation.
    """

    ROLLBACK   = "rollback"
    HOLD       = "hold"
    PROMOTE    = "promote"

    def __init__(
        self,
        thresholds: Optional[CanaryThresholds] = None,
        queue_provider: Optional[QueueMetricsProvider] = None,
    ):
        self._thresholds = thresholds or CanaryThresholds()
        self._queue_provider = queue_provider or QueueMetricsProvider()
        self._check_results: List[CheckResult] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self, m: Optional[CanaryMetrics] = None) -> Tuple[str, List[CheckResult], float]:
        """Run all checks and return (decision, results, score).

        Args:
            m: Metrics snapshot. If None, reads live from the provider.

        Returns:
            decision: one of ROLLBACK / HOLD / PROMOTE
            results:  list of individual CheckResult objects
            score:    overall health score 0.0 – 1.0
        """
        if m is None:
            m = self._queue_provider.snapshot()

        self._check_results = []
        self._run_checks(m)
        score = self._compute_score(m)
        decision = self._decide(score)
        return decision, self._check_results, score

    def check_queue_depth(self, m: CanaryMetrics) -> CheckResult:
        """Canary promotion fails when backlog exceeds threshold."""
        t = self._thresholds
        if m.queue_depth > t.queue_depth_max:
            return CheckResult(
                passed=False,
                name="queue_depth_max",
                message=f"Queue depth {m.queue_depth} exceeds hard max {t.queue_depth_max}",
                detail={"queue_depth": m.queue_depth, "threshold": t.queue_depth_max},
            )
        if m.queue_depth > t.queue_depth_promote:
            return CheckResult(
                passed=False,
                name="queue_depth_promote",
                message=f"Queue depth {m.queue_depth} above promotion threshold {t.queue_depth_promote}",
                detail={"queue_depth": m.queue_depth, "threshold": t.queue_depth_promote},
            )
        return CheckResult(
            passed=True,
            name="queue_depth",
            message=f"Queue depth {m.queue_depth} is healthy",
            detail={"queue_depth": m.queue_depth},
        )

    def check_queue_growth(self, m: CanaryMetrics) -> CheckResult:
        """Detect backlog growth storms."""
        t = self._thresholds
        if m.queue_growth_rate > t.queue_growth_rate_max:
            return CheckResult(
                passed=False,
                name="queue_growth_rate",
                message=f"Queue growth rate {m.queue_growth_rate:.2f} items/s exceeds {t.queue_growth_rate_max}",
                detail={"growth_rate": m.queue_growth_rate, "threshold": t.queue_growth_rate_max},
            )
        return CheckResult(
            passed=True,
            name="queue_growth_rate",
            message=f"Queue growth rate {m.queue_growth_rate:.2f} is acceptable",
            detail={"growth_rate": m.queue_growth_rate},
        )

    def check_processing_latency(self, m: CanaryMetrics) -> CheckResult:
        """High processing latency signals worker starvation / backpressure."""
        t = self._thresholds
        violations = []
        if m.processing_latency_avg > t.processing_latency_max:
            violations.append(f"avg={m.processing_latency_avg:.1f}s > max={t.processing_latency_max}s")
        if m.processing_latency_p99 > t.processing_latency_p99_max:
            violations.append(f"p99={m.processing_latency_p99:.1f}s > p99_max={t.processing_latency_p99_max}s")

        if violations:
            return CheckResult(
                passed=False,
                name="processing_latency",
                message="Processing latency breach: " + "; ".join(violations),
                detail={
                    "avg": m.processing_latency_avg,
                    "p99": m.processing_latency_p99,
                    "thresholds": {"avg_max": t.processing_latency_max, "p99_max": t.processing_latency_p99_max},
                },
            )
        return CheckResult(
            passed=True,
            name="processing_latency",
            message="Processing latency is within bounds",
            detail={"avg": m.processing_latency_avg, "p99": m.processing_latency_p99},
        )

    def check_lease_renewal(self, m: CanaryMetrics) -> CheckResult:
        """Lease renewal failures indicate worker instability — contribute to rollback."""
        t = self._thresholds
        if m.lease_renewal_failure_rate > t.lease_renewal_failure_rate_max:
            return CheckResult(
                passed=False,
                name="lease_renewal_failure_rate",
                message=f"Lease renewal failure rate {m.lease_renewal_failure_rate:.1%} "
                        f"exceeds {t.lease_renewal_failure_rate_max:.1%}",
                detail={
                    "failure_rate": m.lease_renewal_failure_rate,
                    "failures": m.lease_renewal_failures,
                    "total": m.lease_renewal_total,
                    "threshold": t.lease_renewal_failure_rate_max,
                },
            )
        if m.missing_heartbeats > t.lease_renewal_heartbeat_missing_max:
            return CheckResult(
                passed=False,
                name="lease_heartbeat_missing",
                message=f"Missing {m.missing_heartbeats} worker heartbeats "
                        f"(max allowed: {t.lease_renewal_heartbeat_missing_max})",
                detail={"missing": m.missing_heartbeats, "threshold": t.lease_renewal_heartbeat_missing_max},
            )
        return CheckResult(
            passed=True,
            name="lease_renewal",
            message="Lease renewal healthy",
            detail={"failure_rate": m.lease_renewal_failure_rate, "missing_hb": m.missing_heartbeats},
        )

    def check_http_health(self, m: CanaryMetrics) -> CheckResult:
        """Existing HTTP-level checks — preserved for backward compatibility."""
        t = self._thresholds
        if m.http_success_rate < t.http_success_rate_min:
            return CheckResult(
                passed=False,
                name="http_success_rate",
                message=f"HTTP success rate {m.http_success_rate:.1%} below {t.http_success_rate_min:.1%}",
                detail={"success_rate": m.http_success_rate, "threshold": t.http_success_rate_min},
            )
        if m.http_latency_p99 > t.http_latency_p99_max:
            return CheckResult(
                passed=False,
                name="http_latency_p99",
                message=f"HTTP p99 latency {m.http_latency_p99:.3f}s exceeds {t.http_latency_p99_max}s",
                detail={"p99": m.http_latency_p99, "threshold": t.http_latency_p99_max},
            )
        return CheckResult(
            passed=True,
            name="http_health",
            message="HTTP health checks passed",
            detail={"success_rate": m.http_success_rate, "latency_p99": m.http_latency_p99},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_checks(self, m: CanaryMetrics) -> None:
        self._check_results = [
            self.check_queue_depth(m),
            self.check_queue_growth(m),
            self.check_processing_latency(m),
            self.check_lease_renewal(m),
            self.check_http_health(m),
        ]
        for r in self._check_results:
            logger.debug(
                "Canary check %s: %s — %s",
                r.name,
                "PASS" if r.passed else "FAIL",
                r.message,
            )

    def _compute_score(self, m: CanaryMetrics) -> float:
        """Compute an overall health score 0.0-1.0.

        Weighted average of individual dimensions so partial degradation
        is reflected in the composite score.
        """
        checks = self._check_results
        if not checks:
            return 1.0

        weights = {
            "queue_depth":                0.20,
            "queue_growth_rate":           0.10,
            "processing_latency":         0.25,
            "lease_renewal_failure_rate": 0.20,
            "lease_heartbeat_missing":     0.10,
            "http_success_rate":           0.10,
            "http_latency_p99":            0.05,
        }

        score = 0.0
        total_weight = 0.0
        for r in checks:
            w = weights.get(r.name, 0.1)
            score += w if r.passed else 0.0
            total_weight += w

        return score / total_weight if total_weight > 0 else 1.0

    def _decide(self, score: float) -> str:
        t = self._thresholds
        any_fail = any(not r.passed for r in self._check_results)

        if any_fail or score < t.promotion_score_min:
            hard_fail = any(
                not r.passed
                and r.name in {
                    "queue_depth_max",
                    "processing_latency",
                    "lease_renewal_failure_rate",
                    "lease_heartbeat_missing",
                }
                for r in self._check_results
            )
            return self.ROLLBACK if hard_fail else self.HOLD

        return self.PROMOTE

    @property
    def last_results(self) -> List[CheckResult]:
        return self._check_results


# ---------------------------------------------------------------------------
# Metrics emission helpers (to be called from scheduler / worker loops)
# ---------------------------------------------------------------------------

def record_queue_depth(depth: int) -> None:
    """Record current scheduler queue depth."""
    metrics.gauge(QueueMetricsProvider.KEY_QUEUE_DEPTH, float(depth))


def record_queue_growth_rate(rate: float) -> None:
    """Record scheduler queue growth rate (items/second)."""
    metrics.gauge(QueueMetricsProvider.KEY_QUEUE_GROWTH_RATE, float(rate))


def record_processing_latency(latency_seconds: float) -> None:
    """Record a task processing latency sample."""
    metrics.observe(QueueMetricsProvider.KEY_PROCESSING_LAT_AVG, latency_seconds)


def record_processing_latency_p99(p99_seconds: float) -> None:
    """Record scheduler p99 processing latency."""
    metrics.gauge(QueueMetricsProvider.KEY_PROCESSING_LAT_P99, float(p99_seconds))


def record_in_flight(count: int) -> None:
    """Record number of tasks currently in flight."""
    metrics.gauge(QueueMetricsProvider.KEY_IN_FLIGHT, float(count))


def record_lease_renewal(success: bool) -> None:
    """Record a lease renewal attempt outcome."""
    metrics.increment(QueueMetricsProvider.KEY_LEASE_TOTAL)
    if not success:
        metrics.increment(QueueMetricsProvider.KEY_LEASE_FAILURES)


def record_missing_heartbeat() -> None:
    """Increment missing heartbeat counter."""
    metrics.increment(QueueMetricsProvider.KEY_LEASE_MISSED_HB)


def record_http_request(error: bool = False, latency_seconds: float = 0.0) -> None:
    """Record an HTTP health check result."""
    metrics.increment(QueueMetricsProvider.KEY_HTTP_TOTAL)
    if error:
        metrics.increment(QueueMetricsProvider.KEY_HTTP_ERRORS)
    if latency_seconds > 0:
        metrics.observe(QueueMetricsProvider.KEY_HTTP_LAT_P99, latency_seconds)


# 2026-05-26 implementation: queue backlog + lease renewal in canary promotion
