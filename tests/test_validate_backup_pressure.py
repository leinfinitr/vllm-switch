import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.validate_backup_pressure import snapshot, validate_release  # noqa: E402


def test_snapshot_records_logical_and_physical_memory(monkeypatch):
    monkeypatch.setattr("scripts.validate_backup_pressure.mem_available_bytes", lambda: 1000)
    monkeypatch.setattr("scripts.validate_backup_pressure.process_rss_bytes", lambda pid: pid * 10)
    stats = {
        "clients": {
            "c": {
                "pid": 7,
                "total_bytes": 100,
                "released_bytes_total": 20,
                "required_for_restore_bytes": 0,
                "cache_only_bytes": 80,
                "requested_release_bytes_total": 20,
            }
        },
        "stats": {"total_bytes": 100},
        "memory_pressure": {"state": "normal"},
    }
    result = snapshot(stats)
    assert result["memavailable_bytes"] == 1000
    assert result["clients"]["c"]["rss_bytes"] == 70
    assert result["clients"]["c"]["released_bytes_total"] == 20


def test_validate_release_rejects_missing_client_or_nonphysical_release():
    before = {
        "memavailable_bytes": 1000,
        "clients": {
            "c": {
                "pid": 7,
                "rss_bytes": 1000,
                "released_bytes_total": 0,
                "cache_only_bytes": 100,
            }
        },
        "pool_stats": {"pending_release_bytes": 0},
    }
    with pytest.raises(RuntimeError, match="disappeared"):
        validate_release(before, {"memavailable_bytes": 2000, "clients": {}, "pool_stats": {}})
    after = {
        "memavailable_bytes": 1000,
        "clients": {"c": {"pid": 7, "rss_bytes": 1000, "released_bytes_total": 10}},
        "pool_stats": {"pending_release_bytes": 0},
    }
    with pytest.raises(RuntimeError, match="RSS did not decrease"):
        validate_release(before, after)