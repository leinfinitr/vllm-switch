from benchmarks.workload_schema import WorkloadConfig, generate_model_sequence


def test_alternating_sequence_cycles_models():
    config = WorkloadConfig.model_validate(
        {
            "name": "ab",
            "pattern": {"type": "alternating", "models": ["a", "b"]},
            "max_requests": 5,
        }
    )

    assert generate_model_sequence(config) == ["a", "b", "a", "b", "a"]


def test_burst_sequence_repeats_each_model_by_burst_size():
    config = WorkloadConfig.model_validate(
        {
            "name": "burst",
            "pattern": {"type": "burst", "models": ["a", "b"], "burst_size": 2},
            "max_requests": 5,
        }
    )

    assert generate_model_sequence(config) == ["a", "a", "b", "b", "a"]
