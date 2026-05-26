import argparse
import csv
import subprocess
import time
from pathlib import Path


def query_gpu() -> list[dict[str, str]]:
    cmd = [
        "nvidia-smi",
        "--query-gpu=timestamp,index,utilization.gpu,memory.used,memory.total,pcie.link.gen.current",
        "--format=csv,noheader,nounits",
    ]
    output = subprocess.check_output(cmd, text=True)
    rows = []
    for line in output.strip().splitlines():
        parts = [part.strip() for part in line.split(",")]
        timestamp, index, gpu_util, mem_used, mem_total, pcie_gen = parts
        rows.append(
            {
                "timestamp": timestamp,
                "index": index,
                "gpu_util_percent": gpu_util,
                "memory_used_mb": mem_used,
                "memory_total_mb": mem_total,
                "pcie_link_gen_current": pcie_gen,
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect nvidia-smi GPU metrics to CSV")
    parser.add_argument("--output", required=True)
    parser.add_argument("--interval-s", type=float, default=1.0)
    parser.add_argument("--duration-s", type=float, default=60.0)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    end = time.time() + args.duration_s
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = None
        while time.time() < end:
            for row in query_gpu():
                row["sample_unix_s"] = f"{time.time():.6f}"
                if writer is None:
                    writer = csv.DictWriter(f, fieldnames=list(row))
                    writer.writeheader()
                writer.writerow(row)
            f.flush()
            time.sleep(args.interval_s)


if __name__ == "__main__":
    main()
