import random

import numpy as np
import torch

from metropolis import config as config_mod
from metropolis.config import (
    RuntimeConfig,
    apply_runtime_config,
    resolve_device,
    seed_everything,
)


def test_runtime_config_defaults():
    cfg = RuntimeConfig()

    assert cfg.seed == 1337
    assert cfg.device == "auto"
    assert cfg.deterministic is True
    assert cfg.cudnn_benchmark is False
    assert cfg.cudnn_deterministic is True
    assert cfg.test_num_workers == 0


def test_resolve_device_returns_cpu_when_cuda_unavailable(monkeypatch):
    monkeypatch.setattr(config_mod.torch.cuda, "is_available", lambda: False)

    assert resolve_device("auto") == "cpu"


def test_resolve_device_returns_cuda_when_cuda_available(monkeypatch):
    monkeypatch.setattr(config_mod.torch.cuda, "is_available", lambda: True)

    assert resolve_device("auto") == "cuda"


def test_seed_everything_repeats_python_numpy_and_torch_streams():
    # Seeding twice with the same value should reproduce all RNG streams.
    seed_everything(1337, deterministic=True)
    python_first = random.random()
    numpy_first = np.random.rand(3)
    torch_first = torch.rand(3)

    seed_everything(1337, deterministic=True)
    python_second = random.random()
    numpy_second = np.random.rand(3)
    torch_second = torch.rand(3)

    assert python_first == python_second
    assert np.allclose(numpy_first, numpy_second)
    assert torch.equal(torch_first, torch_second)


def test_seed_everything_cudnn_deterministic_defaults_to_deterministic():
    seed_everything(1337, deterministic=True)
    assert torch.backends.cudnn.deterministic is True

    seed_everything(1337, deterministic=False)
    assert torch.backends.cudnn.deterministic is False


def test_apply_runtime_config_applies_cudnn_settings():
    apply_runtime_config(RuntimeConfig(cudnn_benchmark=True, cudnn_deterministic=False))

    assert torch.backends.cudnn.benchmark is True
    assert torch.backends.cudnn.deterministic is False


def test_apply_runtime_config_normalizes_auto_device(monkeypatch):
    monkeypatch.setattr(config_mod.torch.cuda, "is_available", lambda: False)

    normalized = apply_runtime_config(RuntimeConfig(device="auto"))

    assert normalized.device == "cpu"
    assert normalized.seed == 1337
