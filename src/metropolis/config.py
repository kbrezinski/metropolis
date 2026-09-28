from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import random
import torch
import numpy as np

DeviceName = Literal["auto", "cpu", "cuda"]


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    seed: int = 1337
    device: DeviceName = "auto"

    # Reproducibility / determinism knobs
    deterministic: bool = True
    cudnn_benchmark: bool = False
    cudnn_deterministic: bool = True

    # Useful default for simple, reproducible tests/data loading
    test_num_workers: int = 0


def resolve_device(device: DeviceName = "auto") -> DeviceName:
    # Resolve 'auto' to a concrete runtime device string.
    if device == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return device


def seed_everything(
    seed: int,
    deterministic: bool = True,
    cudnn_benchmark: bool = False,
    cudnn_deterministic: bool | None = None,
) -> None:
    # cudnn_deterministic defaults to `deterministic` when not given.

    # Random seeds
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)

    # cuDNN settings matter for reproducibility on CUDA.
    torch.backends.cudnn.benchmark = cudnn_benchmark
    torch.backends.cudnn.deterministic = (
        deterministic if cudnn_deterministic is None else cudnn_deterministic
    )

    # Broader deterministic behavior across PyTorch ops.
    torch.use_deterministic_algorithms(deterministic)


def apply_runtime_config(config: RuntimeConfig) -> RuntimeConfig:

    seed_everything(
        config.seed,
        deterministic=config.deterministic,
        cudnn_benchmark=config.cudnn_benchmark,
        cudnn_deterministic=config.cudnn_deterministic,
    )

    return RuntimeConfig(
        seed=config.seed,
        device=resolve_device(config.device),
        deterministic=config.deterministic,
        cudnn_benchmark=config.cudnn_benchmark,
        cudnn_deterministic=config.cudnn_deterministic,
        test_num_workers=config.test_num_workers,
    )
