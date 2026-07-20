from benchmarks.analyze_results import summarize_events


def test_summarize_events_computes_latency_stats():
    events = [
        {"e2e_ttft_ms": 10.0, "switch_latency_ms": 5.0, "status_code": 200},
        {"e2e_ttft_ms": 30.0, "switch_latency_ms": 15.0, "status_code": 200},
    ]

    summary = summarize_events(events)

    assert summary["requests"] == 2
    assert summary["success"] == 2
    assert summary["e2e_ttft_ms"]["mean"] == 20.0
    assert summary["switch_latency_ms"]["mean"] == 10.0


def test_summarize_events_counts_route_classes_and_keeps_failures_in_denominator():
    events = [
        {"route_class": "steady_resident", "status_code": 200, "queue_wait_ms": 1.0},
        {"route_class": "switch_owner", "status_code": 502, "queue_wait_ms": 3.0},
        {"route_class": "switch_owner", "status_code": None, "error": "timeout"},
    ]

    summary = summarize_events(events)

    assert summary["requests"] == 3
    assert summary["success"] == 1
    assert summary["failed"] == 2
    assert summary["route_classes"] == {"steady_resident": 1, "switch_owner": 2}
    assert summary["queue_wait_ms"]["count"] == 2
