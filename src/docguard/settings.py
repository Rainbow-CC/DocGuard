"""Centralized runtime configuration for DocGuard."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _enabled(name: str, default: bool = False) -> bool:
    return _env(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Environment-specific configuration with portable application defaults."""

    database_path: Path
    log_level: str
    log_file: Path
    log_retention_days: int
    preprocess_command: str
    wsl_distribution: str | None
    result_write_root: Path
    result_agent_root: PurePosixPath
    upload_write_root: Path
    upload_agent_root: PurePosixPath
    upload_max_bytes: int
    skill_agent_root: PurePosixPath
    qwen_api_key: str | None
    qwen_base_url: str
    qwen_vision_model: str
    openclaw_gateway_url: str
    openclaw_api_token: str
    action_chain_export_enabled: bool

    @classmethod
    def from_environment(cls) -> "Settings":
        # This also makes command-line tools and tests read the same .env as the ASGI app.
        load_dotenv(PROJECT_ROOT / ".env")
        runtime_root = PROJECT_ROOT / "data"
        default_result_root = "/var/lib/docguard/results"
        default_upload_root = "/var/lib/docguard/uploads"
        return cls(
            database_path=Path(_env("DOCGUARD_DATABASE_PATH", str(runtime_root / "docguard.sqlite3"))),
            log_level=_env("DOCGUARD_LOG_LEVEL", "INFO"),
            log_file=Path(_env("DOCGUARD_LOG_FILE", str(PROJECT_ROOT / "logs" / "docguard.log"))),
            log_retention_days=int(_env("DOCGUARD_LOG_RETENTION_DAYS", "14")),
            # Bash is a portable Linux/container default. Windows/WSL must configure wsl.exe.
            preprocess_command=_env("DOCGUARD_PREPROCESS_COMMAND", "bash"),
            wsl_distribution=os.getenv("DOCGUARD_WSL_DISTRIBUTION", "").strip() or None,
            result_write_root=Path(_env("DOCGUARD_RESULT_WRITE_ROOT", default_result_root)),
            result_agent_root=PurePosixPath(_env("DOCGUARD_RESULT_AGENT_ROOT", default_result_root)),
            upload_write_root=Path(_env("DOCGUARD_UPLOAD_WRITE_ROOT", default_upload_root)),
            upload_agent_root=PurePosixPath(_env("DOCGUARD_UPLOAD_AGENT_ROOT", default_upload_root)),
            upload_max_bytes=int(_env("DOCGUARD_UPLOAD_MAX_BYTES", str(100 * 1024 * 1024))),
            skill_agent_root=PurePosixPath(_env("DOCGUARD_SKILL_AGENT_ROOT", "/app/doc-audit-integrate-skill")),
            qwen_api_key=os.getenv("DASHSCOPE_API_KEY") or None,
            qwen_base_url=_env("DOCGUARD_QWEN_BASE_URL", "https://llm-qk9l4nr6p8kz0huk.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"),
            qwen_vision_model=_env("DOCGUARD_QWEN_VISION_MODEL", "qwen3.7-flash"),
            openclaw_gateway_url=_env("OPENCLAW_GATEWAY_URL", "").rstrip("/"),
            openclaw_api_token=_env("OPENCLAW_API_TOKEN", ""),
            action_chain_export_enabled=_enabled("DOCGUARD_ACTION_CHAIN_EXPORT_ENABLED"),
        )
