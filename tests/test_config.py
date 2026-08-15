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


def test_load_config_duplicate_creator_rejected(tmp_path):
    # Same (service, id) twice would silently collide on one state.json
    # key (see state.creator_key), so this must fail loudly instead.
    path = _write(
        tmp_path,
        {
            "creators": [
                {"service": "patreon", "id": "123", "name": "First"},
                {"service": "patreon", "id": "123", "name": "Second"},
            ]
        },
    )
    with pytest.raises(ConfigError, match="duplicate creator"):
        load_config(path)


def test_load_config_same_id_different_service_is_allowed(tmp_path):
    # Same numeric id on two different services is a different creator
    # entirely and must not trip the duplicate check.
    path = _write(
        tmp_path,
        {
            "creators": [
                {"service": "patreon", "id": "123", "name": "Patreon Creator"},
                {"service": "subscribestar", "id": "123", "name": "SubscribeStar Creator"},
            ]
        },
    )
    config = load_config(path)
    assert len(config.creators) == 2


def test_load_config_non_numeric_max_preview_chars_rejected(tmp_path):
    path = _write(
        tmp_path,
        {
            "settings": {"max_preview_chars": "not-a-number"},
            "creators": [{"service": "patreon", "id": "1"}],
        },
    )
    with pytest.raises(ConfigError, match="max_preview_chars"):
        load_config(path)


def test_load_config_non_numeric_heartbeat_interval_rejected(tmp_path):
    path = _write(
        tmp_path,
        {
            "settings": {"heartbeat": {"interval_hours": "soon"}},
            "creators": [{"service": "patreon", "id": "1"}],
        },
    )
    with pytest.raises(ConfigError, match="interval_hours"):
        load_config(path)


def test_load_config_zero_heartbeat_interval_rejected(tmp_path):
    path = _write(
        tmp_path,
        {
            "settings": {"heartbeat": {"interval_hours": 0}},
            "creators": [{"service": "patreon", "id": "1"}],
        },
    )
    with pytest.raises(ConfigError, match="interval_hours"):
        load_config(path)


def test_load_config_negative_max_preview_chars_rejected(tmp_path):
    path = _write(
        tmp_path,
        {
            "settings": {"max_preview_chars": -5},
            "creators": [{"service": "patreon", "id": "1"}],
        },
    )
    with pytest.raises(ConfigError, match="max_preview_chars"):
        load_config(path)


def test_load_config_max_preview_chars_zero_is_allowed(tmp_path):
    # 0 is a valid (if unusual) "no preview text" setting and should
    # not be rejected the way a negative value is.
    path = _write(
        tmp_path,
        {
            "settings": {"max_preview_chars": 0},
            "creators": [{"service": "patreon", "id": "1"}],
        },
    )
    config = load_config(path)
    assert config.settings.max_preview_chars == 0
