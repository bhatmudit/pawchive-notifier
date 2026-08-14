import json

import pytest

from config import ConfigError, load_config


def _write(tmp_path, data):
    path = tmp_path / "creators.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_config_valid(tmp_path):
    path = _write(
        tmp_path,
        {
            "settings": {"notify_edits": True, "max_preview_chars": 100},
            "creators": [{"service": "patreon", "id": "123", "name": "Someone"}],
        },
    )
    config = load_config(path)

    assert len(config.creators) == 1
    assert config.creators[0].name == "Someone"
    assert config.settings.notify_edits is True
    assert config.settings.max_preview_chars == 100
    # unspecified settings fall back to defaults
    assert config.settings.startup_email is True
    assert config.settings.heartbeat.enabled is False


def test_load_config_defaults_name_from_service_and_id(tmp_path):
    path = _write(tmp_path, {"creators": [{"service": "patreon", "id": "123"}]})
    config = load_config(path)
    assert config.creators[0].name == "patreon/123"


def test_load_config_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "does-not-exist.json")


def test_load_config_not_json(tmp_path):
    path = tmp_path / "creators.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_missing_creators_key(tmp_path):
    path = _write(tmp_path, {"settings": {}})
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_empty_creators_list(tmp_path):
    path = _write(tmp_path, {"creators": []})
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_creator_missing_service(tmp_path):
    path = _write(tmp_path, {"creators": [{"id": "123"}]})
    with pytest.raises(ConfigError, match="creators\\[0\\]"):
        load_config(path)


def test_load_config_creator_missing_id(tmp_path):
    path = _write(tmp_path, {"creators": [{"service": "patreon"}]})
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_creator_not_an_object(tmp_path):
    path = _write(tmp_path, {"creators": ["patreon/123"]})
    with pytest.raises(ConfigError):
        load_config(path)
