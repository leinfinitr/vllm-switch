import pytest
from pydantic import ValidationError

from controller.config import ControllerConfig, load_config

EXPECTED_DISK_BACKUP_DIR = "/home/ljl/research-systems/vllm-model-switch-controller/tmp"


def test_load_config_parses_models_and_controller(tmp_path):
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
models:
  a:
    backend_url: http://127.0.0.1:8101
    served_model_name: model-a
controller:
  port: 9000
  startup_awake_model: a
  metrics_path: results/events.jsonl
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert isinstance(config, ControllerConfig)
    assert config.controller.port == 9000
    assert config.controller.startup_awake_model == "a"
    assert config.models["a"].backend_url == "http://127.0.0.1:8101"
    assert config.models["a"].sleep_level == 1


def test_config_rejects_unknown_startup_model(tmp_path):
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
models:
  a:
    backend_url: http://127.0.0.1:8101
    served_model_name: model-a
controller:
  startup_awake_model: missing
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="startup_awake_model"):
        load_config(config_path)


def test_model_disk_backup_env_defaults_to_project_tmp():
    config = ControllerConfig.model_validate(
        {
            "models": {
                "a": {
                    "backend_url": "http://a",
                    "served_model_name": "a",
                }
            }
        }
    )

    assert config.models["a"].env["VLLM_CPU_BACKUP_DISK_DIR"] == EXPECTED_DISK_BACKUP_DIR


def test_model_disk_backup_env_keeps_explicit_override():
    config = ControllerConfig.model_validate(
        {
            "models": {
                "a": {
                    "backend_url": "http://a",
                    "served_model_name": "a",
                    "env": {"VLLM_CPU_BACKUP_DISK_DIR": "/mnt/backup-a"},
                }
            }
        }
    )

    assert config.models["a"].env["VLLM_CPU_BACKUP_DISK_DIR"] == "/mnt/backup-a"


@pytest.mark.parametrize(
    "controller",
    [
        {"cpu_memory_reclaim_available_ratio": 0.10},
        {"cpu_memory_recovery_available_ratio": 0.20},
        {"cpu_memory_recovery_available_bytes": 1024},
    ],
)
def test_config_rejects_incomplete_memory_pressure_pairs(controller):
    with pytest.raises(ValidationError):
        ControllerConfig.model_validate(
            {
                "models": {
                    "a": {
                        "backend_url": "http://a",
                        "served_model_name": "a",
                    }
                },
                "controller": controller,
            }
        )
