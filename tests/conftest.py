"""Test isolation: never touch the user's real ~/.config/solitaire."""

import pytest

import solitaire_app.config as cfg


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(cfg, "STATS_FILE", tmp_path / "stats.json")
    monkeypatch.setattr(cfg, "SAVE_FILE", tmp_path / "saved_game.json")
