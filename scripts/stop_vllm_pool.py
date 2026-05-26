import argparse
import json
import os
import signal
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Stop vLLM pool processes from a pid file")
    parser.add_argument("--pid-file", default="pids.json")
    parser.add_argument("--timeout-s", type=float, default=30)
    args = parser.parse_args()
    path = Path(args.pid_file)
    if not path.exists():
        print(f"pid file does not exist: {path}")
        return
    pids = json.loads(path.read_text(encoding="utf-8"))
    for name, pid in pids.items():
        try:
            print(f"terminating {name} pid={pid}")
            os.kill(int(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + args.timeout_s
    for name, pid in pids.items():
        while time.time() < deadline:
            try:
                os.kill(int(pid), 0)
            except ProcessLookupError:
                break
            time.sleep(0.5)
        else:
            try:
                print(f"killing {name} pid={pid}")
                os.kill(int(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
    path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
