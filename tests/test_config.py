import random

import numpy as np
import torch

from metropolis import config as config_mod
from metropolis.config import (
    RuntimeConfig,
    apply_runtime_config,
    seed_everything,
)


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


def test_apply_runtime_config_normalizes_auto_device(monkeypatch):
    monkeypatch.setattr(config_mod.torch.cuda, "is_available", lambda: False)

    normalized = apply_runtime_config(RuntimeConfig(device="auto"))

    assert normalized.device == "cpu"
    assert normalized.seed == 1337
