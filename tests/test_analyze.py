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
