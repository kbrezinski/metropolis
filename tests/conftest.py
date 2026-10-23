import os
from pathlib import Path

# These must be set before torch initializes CUDA-related backends.
os.environ.setdefault("PYTHONHASHSEED", "0")
os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
# Keep test-generated compiler files in the ignored project environment.
os.environ.setdefault(
    "TORCHINDUCTOR_CACHE_DIR",
    str(Path(__file__).resolve().parents[1] / ".venv" / "torchinductor"),
)

import random

import numpy as np
import pytest
import torch

TEST_SEED = 1234


@pytest.fixture(autouse=True)
def reproducible_test_environment():
    """Reset RNG state and enable deterministic PyTorch behavior for each test."""
    # Reseed all RNGs per test so model init, sampling, and dropout are repeatable.
    random.seed(TEST_SEED)
    np.random.seed(TEST_SEED)
    torch.manual_seed(TEST_SEED)

    # Disable cuDNN autotuning, which can choose different kernels across runs.
    torch.backends.cudnn.benchmark = False

    # Prefer deterministic cuDNN implementations when available.
    torch.backends.cudnn.deterministic = True

    # Fail fast if a test uses a PyTorch op without a deterministic implementation.
    torch.use_deterministic_algorithms(True)
