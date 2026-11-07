"""All runtime settings. Every value can be set with an IFIC_* environment variable or .env."""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from croniter import croniter
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IFIC_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///data/ops.db"
    data_dir: Path = Path("data")
    api_key: str | None = None  # write endpoints refuse every request until this is set
    workers_enabled: bool = True
    worker_poll_s: float = 5.0

    # sync
    sync_cron: str = "0 */6 * * *"  # UTC. Issues appear every two weeks; this finds one the same day
    retry_interval_s: int = 15 * 60  # sooner than the next scheduled run after a run with errors
    min_issue: int = 3074  # the oldest issue the challenge evaluates; older ones can be uploaded
    stale_after_days: int = 21  # observed gaps are 14-17 days
    space_index_base: str = "https://www.itu.int/sns/wic/"
    terrestrial_index_url: str = "https://www.itu.int/en/ITU-R/terrestrial/brific/Pages/default.aspx"
    terrestrial_zip_pattern: str = "https://www.itu.int/en/ITU-R/terrestrial/brific/BRIFIC/MDB/brific{issue}.zip"
    http_timeout_s: float = 60.0
    user_agent: str = "ific-guard/0.1"
    max_zip_bytes: int = 2 * 1024**3
    max_mdb_bytes: int = 6 * 1024**3

    # screening
    m4_filter_path: Path = Path("data/national/m4_bands.csv")
    # GSO arc visible from Finland (RR geometry, elevation 0 deg); see docs/services-architecture.md
    gso_visible_west_deg: float = -47.0
    gso_visible_east_deg: float = 97.0
    ngso_min_inclination_deg: float = 55.0
    comment_window_months: int = 4
    pfd_margin_weight: float = 10.0
    pfd_special_interest_weight: float = 20.0
    pfd_primary_allocation_weight: float = 10.0
    pfd_passive_band_weight: float = 30.0
    pfd_near_limit_weight: float = 15.0
    pfd_envelope_margin_factor: float = 0.5

    # notifier
    email_backend: Literal["console", "file", "smtp"] = "console"
    mail_from: str = "ific-guard@localhost"
    mail_to: list[str] = []
    ops_mail_to: list[str] = []
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    notify_min_severity: Literal["low", "medium", "high"] = "medium"
    digest_max_cases: int = 50
    public_base_url: str = "http://localhost:8002"

    # terrestrial: administrations whose notices are in scope (ITU codes: Russia, Sweden, Estonia, Norway, Denmark)
    neighbour_adms: list[str] = ["RUS", "S", "EST", "NOR", "DNK"]
    # Screening distance only; this is an engineering default, not a regulatory threshold.
    # Keys are "default" or inclusive frequency ranges such as "380-400" (MHz).
    terrestrial_distance_limits_km: dict[str, float] = {"default": 300.0}

    @field_validator("sync_cron")
    @classmethod
    def _valid_cron(cls, value: str) -> str:
        if not croniter.is_valid(value):
            raise ValueError(f"IFIC_SYNC_CRON is not a valid cron expression: {value!r}")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
