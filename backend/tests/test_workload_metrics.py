from datetime import UTC, datetime

from app.services.workload_metrics import WorkloadMetricService


def test_workload_metric_normalization_preserves_missing_and_peak_inputs() -> None:
    normalized = WorkloadMetricService.normalize(
        {
            "time": 1785067379,
            "cpu": 0.25,
            "mem": 4_294_967_296,
            "diskread": 1024,
            "diskwrite": None,
            "netin": 2048,
            "netout": -1,
        }
    )
    assert normalized is not None
    assert normalized["bucket_at"] == datetime(2026, 7, 26, 12, 2, tzinfo=UTC)
    assert normalized["cpu_avg"] == normalized["cpu_max"] == 0.25
    assert normalized["memory_used_avg"] == 4_294_967_296
    assert normalized["disk_write_avg"] is None
    assert normalized["network_transmit_avg"] is None


def test_workload_metric_normalization_rejects_missing_timestamp() -> None:
    assert WorkloadMetricService.normalize({"cpu": 0.2}) is None
