import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

METRIC_KEYS = [
    "e2e_ttft_ms",
    "e2e_latency_ms",
    "switch_latency_ms",
    "sleep_latency_ms",
    "wake_latency_ms",
    "backend_ttft_ms",
]


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "requests": len(events),
        "success": sum(1 for e in events if e.get("status_code") and int(e["status_code"]) < 400),
        "errors": sum(1 for e in events if e.get("error")),
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
