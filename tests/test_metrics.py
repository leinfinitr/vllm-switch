import json

from controller.metrics import MetricsRecorder, RequestMetrics


def test_metrics_recorder_appends_jsonl(tmp_path):
    path = tmp_path / "events.jsonl"
    recorder = MetricsRecorder(path)
    metrics = RequestMetrics(request_id="r1", model="a", path="/v1/chat/completions")
    metrics.switch_needed = True
    metrics.switch_latency_ms = 12.5

    recorder.record(metrics)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["request_id"] == "r1"
    assert payload["model"] == "a"
    assert payload["switch_needed"] is True
    assert payload["switch_latency_ms"] == 12.5
