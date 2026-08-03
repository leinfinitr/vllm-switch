# Documentation

This directory contains the current operating and architecture documentation for the
vLLM Model Switch Controller.

## Start Here

- [Getting started](getting-started.md): install the controller, configure backends,
  start the pool, and send the first request.
- [Configuration reference](configuration.md): model and controller settings, defaults,
  validation rules, and local-file conventions.
- [Operations and validation](operations.md): health checks, smoke tests, workload tools,
  telemetry, shutdown, and troubleshooting.

## Reference

- [API reference](api.md): supported OpenAI-compatible and administrative endpoints.
- [Architecture](architecture.md): ownership boundaries, request switching, failure
  behavior, and relationships to the companion repositories.
- [CPU backup coordinator](cpu_backup_coordinator.md): aggregate accounting protocol,
  release acknowledgement, pressure policy, and physical-reclaim evidence.

## Archive

[`archive/`](archive/) contains completed plans and historical experiment reports.
Archived commands, machine paths, commits, and schemas describe the system at the time
of each record; they are not current operating instructions.

Current behavior is defined by the root [README](../README.md), the active documents in
this directory, configuration validation in `controller/config.py`, and CLI `--help`
output.
