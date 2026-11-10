import sqlite3

import pytest

from jarvis.memory import MemoryStore


def test_reopen_persists_fact_and_episode(tmp_path):
    path = tmp_path / "memory.sqlite3"
    first = MemoryStore(path)
    fact = first.remember("Ich bevorzuge kurze Statusberichte", "preference")
    ep = first.record_turn("room-a", "Bitte lokal speichern", "Erledigt.")
    second = MemoryStore(path)
    assert second.search("Statusberichte", 5)["facts"][0]["id"] == fact["id"]
    assert second.recent_episodes("room-a")[0]["id"] == ep["id"]


def test_search_delete_invalidates_cache(tmp_path):
    db = MemoryStore(tmp_path / "m.sqlite3")
    note = db.remember("Projekt Apollo hat Priorität", "project")
    assert db.search("Apollo")["facts"]
    assert db.forget(note["id"]) is True
    assert not db.search("Apollo")["facts"]
    assert db.forget(note["id"]) is False


def test_episode_secrets_are_redacted_and_direct_notes_rejected(tmp_path):
    db = MemoryStore(tmp_path / "m.sqlite3")
    db.record_turn("c", "Mein Passwort: Geheim123", "API key: sk-test-123456789012")
    episode = db.recent_episodes("c")[0]
    assert "Geheim123" not in episode["user_text"]
    assert "sk-test" not in episode["assistant_text"]
    try:
        db.remember("password: Geheim123")
    except ValueError:
        pass
    else:
        raise AssertionError("direct credential note must be rejected")


def test_limit_is_bounded_and_snapshot_shape(tmp_path):
    db = MemoryStore(tmp_path / "m.sqlite3")
    db.remember("Eine lokale Präferenz")
    result = db.snapshot("lokale", 999)
    assert set(("facts", "episodes", "stats", "events")) <= result.keys()
    assert result["stats"]["facts"] == 1


def test_separate_store_cache_sees_write_and_delete(tmp_path):
    path = tmp_path / "shared.sqlite3"
    reader = MemoryStore(path)
    writer = MemoryStore(path)
    assert reader.search("cross process cache")["facts"] == []
    note = writer.remember("cross process cache invalidation")
    assert reader.search("cross process cache")["facts"][0]["id"] == note["id"]
    assert writer.forget(note["id"])
    assert reader.search("cross process cache")["facts"] == []


def test_rare_old_fact_survives_natural_query_candidate_limit(tmp_path):
    db = MemoryStore(tmp_path / "long-tail.sqlite3")
    old = db.remember("The archival codename is zirconium lighthouse")
    for index in range(120):
        db.remember(f"generic project status update item {index}")
    result = db.search("archival codename project status update", 12)
    assert old["id"] in {fact["id"] for fact in result["facts"]}


def test_scopes_protect_facts_episodes_and_events(tmp_path):
    path = tmp_path / "scoped.sqlite3"
    owner_a = MemoryStore(path, owner_scope="A", installation_scope="one")
    owner_b = MemoryStore(path, owner_scope="B", installation_scope="one")
    fact = owner_a.remember("A private fact")
    owner_a.record_turn("private", "A question", "A answer")
    assert owner_b.search("private fact")["facts"] == []
    assert owner_b.recent_episodes() == []
    assert owner_b.events() == []
    assert owner_b.stats()["facts"] == 0
    assert owner_b.stats()["episodes"] == 0
    assert owner_b.stats()["events"] == 0
    assert owner_b.forget(fact["id"]) is False


def test_connect_closes_sqlite_handle(tmp_path):
    db = MemoryStore(tmp_path / "handles.sqlite3")
    with db._connect() as connection:
        connection.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
