import json

from state import empty_state, get_creator_state, load_state, save_state


def test_load_state_missing_file_returns_empty_state(tmp_path):
    state = load_state(tmp_path / "missing.json")
    assert state == empty_state()


def test_load_state_migrates_old_schema(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "meta": {"consecutive_failures": 3},  # old, redundant global counter
                "creators": {"patreon:123": {}},  # missing bootstrapped/posts/etc.
            }
        ),
        encoding="utf-8",
    )

    state = load_state(path)

    assert "consecutive_failures" not in state["meta"]
    assert state["meta"]["welcomed"] is False
    entry = state["creators"]["patreon:123"]
    assert entry["bootstrapped"] is False
    assert entry["consecutive_failures"] == 0
    assert entry["posts"] == {}


def test_save_state_is_atomic_and_roundtrips(tmp_path):
    path = tmp_path / "nested" / "state.json"
    state = empty_state()
    state["creators"]["patreon:1"] = {"bootstrapped": True, "consecutive_failures": 0, "posts": {}}

    save_state(path, state)

    assert path.exists()
    assert not path.with_suffix(".json.tmp").exists()
    assert json.loads(path.read_text()) == state


def test_get_creator_state_creates_and_reuses_entry():
    state = empty_state()

    entry = get_creator_state(state, "patreon", "123")
    entry["consecutive_failures"] = 2

    same_entry = get_creator_state(state, "patreon", "123")
    assert same_entry["consecutive_failures"] == 2
    assert state["creators"] == {"patreon:123": same_entry}


def test_get_creator_state_fills_defaults_on_partial_existing_entry():
    # Simulates a hand-edited or partially-migrated state.json where the
    # creator key exists but is missing some of the expected fields.
    state = empty_state()
    state["creators"]["patreon:123"] = {"bootstrapped": True}

    entry = get_creator_state(state, "patreon", "123")

    assert entry["bootstrapped"] is True  # existing value preserved
    assert entry["consecutive_failures"] == 0  # missing value filled in
    assert entry["posts"] == {}


def test_get_creator_state_distinguishes_same_id_different_service():
    state = empty_state()
    patreon_entry = get_creator_state(state, "patreon", "1")
    other_entry = get_creator_state(state, "subscribestar", "1")

    patreon_entry["consecutive_failures"] = 5
    assert other_entry["consecutive_failures"] == 0
    assert set(state["creators"]) == {"patreon:1", "subscribestar:1"}
