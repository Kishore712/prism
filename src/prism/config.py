"""Environment-backed runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8765
    log_level: str = "INFO"
    mcp_allowed_hosts: tuple[str, ...] = ()
    mcp_allowed_origins: tuple[str, ...] = ()
    public_url: str | None = None
    owner_label: str = "the Prism owner"
    trust_forwarded_for: bool = False

    @property
    def database_path(self) -> Path:
        return self.data_dir / "prism.db"

    @property
    def blob_root(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def backup_root(self) -> Path:
        return self.data_dir / "backups"

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.blob_root.mkdir(parents=True, exist_ok=True)
        self.backup_root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("PRISM_DATA_DIR", "./var")).expanduser().resolve()
        return cls(
            data_dir=data_dir,
            host=os.getenv("PRISM_HOST", "127.0.0.1"),
            port=int(os.getenv("PRISM_PORT", "8765")),
            log_level=os.getenv("PRISM_LOG_LEVEL", "INFO"),
            mcp_allowed_hosts=cls._csv("PRISM_MCP_ALLOWED_HOSTS"),
            mcp_allowed_origins=cls._csv("PRISM_MCP_ALLOWED_ORIGINS"),
            public_url=os.getenv("PRISM_PUBLIC_URL") or None,
            owner_label=os.getenv("PRISM_OWNER_LABEL") or "the Prism owner",
            trust_forwarded_for=os.getenv("PRISM_TRUST_FORWARDED_FOR", "").lower()
            in {"1", "true", "yes"},
        )

    @staticmethod
    def _csv(name: str) -> tuple[str, ...]:
        return tuple(
            item.strip()
            for item in os.getenv(name, "").split(",")
            if item.strip()
        )
