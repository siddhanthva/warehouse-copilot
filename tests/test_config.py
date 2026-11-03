"""Settings must treat empty environment values as unset (GitHub Actions sets undefined variables to "")."""

from __future__ import annotations

from warehouse_copilot import config


def test_empty_env_values_fall_back_to_defaults(monkeypatch):
    for key in ("GEMINI_MODEL", "GEMINI_MODEL_ALT", "GOOGLE_CLOUD_LOCATION", "APPROVAL_ROW_THRESHOLD", "LANGFUSE_BASE_URL"):
        monkeypatch.setenv(key, "")
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.gemini_model == config.Settings.gemini_model and s.gemini_model
    assert s.gemini_model_alt
    assert s.google_cloud_location == "global"
    assert s.approval_row_threshold == 2_000_000
    assert s.langfuse_host.startswith("https://")


def test_set_env_values_still_win(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "my-model")
    config.get_settings.cache_clear()
    assert config.get_settings().gemini_model == "my-model"
