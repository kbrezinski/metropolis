"""Small shared contracts for the standalone pilot."""

from dataclasses import asdict, dataclass, field
import hashlib
import json


@dataclass(frozen=True)
class Task:
    id: str
    family: str
    prompt: str
    starter_files: dict[str, str] = field(default_factory=dict)
    version: str = "1.0"
    parameters: dict = field(default_factory=dict)

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


@dataclass
class Grade:
    passed: bool
    checks: list[dict] = field(default_factory=list)
    infrastructure_error: str | None = None
    outcome: str | None = None

    def __post_init__(self):
        if self.outcome is None:
            self.outcome = "Full" if self.passed else "Failure"

    def to_dict(self) -> dict:
        return asdict(self)


class InfrastructureError(RuntimeError):
    """The benchmark environment or provider cannot run an attempt."""


class AttemptLimit(RuntimeError):
    """An agent exhausted its declared budget."""
