import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

METRIC_KEYS = [
    "e2e_response_body_first_byte_ms",
    "e2e_latency_ms",
    "switch_latency_ms",
    "sleep_latency_ms",
    "wake_latency_ms",
    "response_body_first_byte_ms",
    "queue_wait_ms",
    "request_drain_ms",
]


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    events = [
        event
        for event in events
        if str(event.get("path", "")).startswith("/v1/")
    ]
    success = sum(
        1
        for event in events
        if event.get("status_code") is not None
        and 200 <= int(event["status_code"]) < 400
        and not event.get("error")
    )
    route_classes: dict[str, int] = {}
    for event in events:
        route_class = event.get("route_class")
        if route_class:
            route_classes[str(route_class)] = route_classes.get(str(route_class), 0) + 1
    summary: dict[str, Any] = {
        "requests": len(events),
        "success": success,
        "failed": len(events) - success,
        "errors": sum(1 for e in events if e.get("error")),
        "route_classes": route_classes,
    }
    for key in METRIC_KEYS:
        values = [float(e[key]) for e in events if e.get(key) is not None]
        if values:
            arr = np.array(values, dtype=float)
            summary[key] = {
                "count": int(arr.size),
                "mean": float(np.mean(arr)),
                "p50": float(np.percentile(arr, 50)),
                "p95": float(np.percentile(arr, 95)),
                "p99": float(np.percentile(arr, 99)),
                "min": float(np.min(arr)),
                "max": float(np.max(arr)),
            }
        else:
            summary[key] = {"count": 0}
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze controller/workload JSONL results")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    events = load_jsonl(args.input)
    summary = summarize_events(events)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
