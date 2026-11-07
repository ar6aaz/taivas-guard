import io
import zipfile

import pytest

from ific_guard.config import Settings, get_settings
from ific_guard.db import init_db, reset_caches

# A developer's .env must not change what the tests see, such as a real API key.
Settings.model_config["env_file"] = None


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """Every test gets its own database and data folder, and can never send real mail."""
    monkeypatch.setenv("IFIC_DATABASE_URL", f"sqlite:///{tmp_path}/ops.db")
    monkeypatch.setenv("IFIC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("IFIC_API_KEY", "test-key")
    monkeypatch.setenv("IFIC_WORKERS_ENABLED", "false")
    monkeypatch.setenv("IFIC_EMAIL_BACKEND", "file")
    monkeypatch.setenv("IFIC_MAIL_TO", '["expert@example.org"]')
    monkeypatch.setenv("IFIC_OPS_MAIL_TO", '["ops@example.org"]')
    monkeypatch.setenv("IFIC_MIN_ISSUE", "3082")
    get_settings.cache_clear()
    reset_caches()
    init_db()
    yield
    get_settings.cache_clear()
    reset_caches()


def make_zip(name="ific3083.mdb", content=b"\x00\x01\x00\x00Standard Jet DB\x00" + b"x" * 64) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, content)
    return buffer.getvalue()
