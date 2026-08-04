import json
import signal

from scripts import stop_vllm_pool


def write_pid_file(path, *, pid=123, pgid=123, start_time_ticks=456):
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "processes": {
                    "model-a": {
                        "pid": pid,
                        "pgid": pgid,
                        "start_time_ticks": start_time_ticks,
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_stop_owned_processes_terminates_verified_process_group(tmp_path, monkeypatch):
    pid_file = tmp_path / "pids.json"
    write_pid_file(pid_file)
    signals = []

    monkeypatch.setattr(
        stop_vllm_pool,
        "read_process_identity",
        lambda pid: stop_vllm_pool.ProcessIdentity(pid, 123, 456),
    )
    monkeypatch.setattr(stop_vllm_pool.os, "getpgrp", lambda: 999)
    monkeypatch.setattr(
        stop_vllm_pool.os,
        "killpg",
        lambda pgid, sig: signals.append((pgid, sig)),
    )
    monkeypatch.setattr(stop_vllm_pool, "wait_process_group_empty", lambda *_args: True)

    assert stop_vllm_pool.stop_owned_processes(pid_file, timeout_s=1) is True
    assert signals == [(123, signal.SIGTERM)]
    assert not pid_file.exists()


def test_stop_owned_processes_refuses_reused_pid(tmp_path, monkeypatch):
    pid_file = tmp_path / "pids.json"
    write_pid_file(pid_file)
    signals = []

    monkeypatch.setattr(
        stop_vllm_pool,
        "read_process_identity",
        lambda pid: stop_vllm_pool.ProcessIdentity(pid, 123, 999),
    )
    monkeypatch.setattr(stop_vllm_pool.os, "getpgrp", lambda: 888)
    monkeypatch.setattr(
        stop_vllm_pool.os,
        "killpg",
        lambda pgid, sig: signals.append((pgid, sig)),
    )

    assert stop_vllm_pool.stop_owned_processes(pid_file, timeout_s=1) is False
    assert signals == []
    assert pid_file.exists()


def test_stop_owned_processes_refuses_legacy_unverifiable_pid_file(tmp_path):
    pid_file = tmp_path / "pids.json"
    pid_file.write_text('{"model-a": 123}', encoding="utf-8")

    assert stop_vllm_pool.stop_owned_processes(pid_file, timeout_s=1) is False
    assert pid_file.exists()


def test_stop_owned_processes_escalates_verified_group(tmp_path, monkeypatch):
    pid_file = tmp_path / "pids.json"
    write_pid_file(pid_file)
    signals = []
    waits = iter([False, True])

    monkeypatch.setattr(
        stop_vllm_pool,
        "read_process_identity",
        lambda pid: stop_vllm_pool.ProcessIdentity(pid, 123, 456),
    )
    monkeypatch.setattr(stop_vllm_pool.os, "getpgrp", lambda: 999)
    monkeypatch.setattr(
        stop_vllm_pool.os,
        "killpg",
        lambda pgid, sig: signals.append((pgid, sig)),
    )
    monkeypatch.setattr(
        stop_vllm_pool,
        "wait_process_group_empty",
        lambda *_args: next(waits),
    )

    assert stop_vllm_pool.stop_owned_processes(pid_file, timeout_s=1) is True
    assert signals == [(123, signal.SIGTERM), (123, signal.SIGKILL)]
    assert not pid_file.exists()
