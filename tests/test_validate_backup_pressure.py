import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.validate_backup_pressure import snapshot, validate_release  # noqa: E402


def test_snapshot_records_logical_and_process_tree_memory(monkeypatch):
    monkeypatch.setattr("scripts.validate_backup_pressure.mem_available_bytes", lambda: 1000)
    monkeypatch.setattr("scripts.validate_backup_pressure.process_rss_bytes", lambda pid: pid * 10)
    monkeypatch.setattr(
        "scripts.validate_backup_pressure.process_tree_rss_bytes", lambda pid: pid * 20
    )
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
    assert result["clients"]["c"]["process_tree_rss_bytes"] == 140


def test_validate_release_requires_ack_and_material_physical_release(monkeypatch):
    monkeypatch.setattr("scripts.validate_backup_pressure.Path.exists", lambda _path: True)
    before = {
        "memavailable_bytes": 1000,
        "clients": {
            "c": {
                "pid": 7,
                "rss_bytes": 1000,
                "process_tree_rss_bytes": 2000,
                "released_bytes_total": 0,
                "requested_release_bytes_total": 0,
                "cache_only_bytes": 100,
            }
        },
        "pool_stats": {"pending_release_bytes": 0},
    }
    after = {
        "memavailable_bytes": 1100,
        "clients": {
            "c": {
                "pid": 7,
                "rss_bytes": 999,
                "process_tree_rss_bytes": 1999,
                "released_bytes_total": 100,
                "requested_release_bytes_total": 100,
            }
        },
        "pool_stats": {"pending_release_bytes": 0},
    }
    with pytest.raises(RuntimeError, match="acknowledgement"):
        validate_release(before, after)
    with pytest.raises(RuntimeError, match="process-tree RSS"):
        validate_release(before, after, {"ok": True, "queued_bytes": 100})

    after["clients"]["c"]["process_tree_rss_bytes"] = 1900
    validate_release(before, after, {"ok": True, "queued_bytes": 100})
