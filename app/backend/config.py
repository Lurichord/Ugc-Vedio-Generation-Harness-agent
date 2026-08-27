from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    repo_root: Path
    output_root: Path
    data_root: Path
    intake_root: Path
    static_root: Path

    @classmethod
    def default(cls) -> "AppConfig":
        repo_root = Path(__file__).resolve().parents[2]
        app_root = repo_root / "app"
        return cls(
            repo_root=repo_root,
            output_root=repo_root / "outputs",
            data_root=app_root / "data" / "projects",
            intake_root=app_root / "data" / "intake",
            static_root=app_root / "frontend",
        )

