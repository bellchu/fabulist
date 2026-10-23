"""The settings and provider store refuses what it cannot do, and says why; the first start seeds the providers it should."""
import pytest

from app import config, db, settings

OPENAI = {"name": "Corp gateway", "kind": "openai", "base_url": "https://llm.example.com/v1", "model": "gpt-x"}


@pytest.fixture
def store(client, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_KEY", "unit-test-key")
    yield
    for row in db.rows("SELECT id FROM providers WHERE name <> 'Offline heuristic'"):
        db.execute("DELETE FROM providers WHERE id = %s", (row["id"],))
    settings.update_settings({"embed_provider_id": ""})
    settings.seed()


def test_an_api_key_cannot_be_stored_without_a_server_encryption_key(store, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_KEY", "")
    with pytest.raises(settings.SettingsError, match="SETTINGS_ENCRYPTION_KEY"):
        settings.save_provider(dict(OPENAI, api_key="sk-secret"))


def test_a_key_stored_under_another_encryption_key_is_reported_not_garbled(store, monkeypatch):
    saved = settings.save_provider(dict(OPENAI, api_key="sk-secret"))
    monkeypatch.setattr(config, "SETTINGS_KEY", "a-different-key")
    with pytest.raises(settings.SettingsError, match="cannot be decrypted"):
        settings.get_provider(saved["id"])


def test_provider_input_is_checked_with_a_reason_each_time(store):
    with pytest.raises(settings.SettingsError, match="Model is required"):
        settings.save_provider(dict(OPENAI, model=""))
    with pytest.raises(settings.SettingsError, match="Provider not found"):
        settings.save_provider(OPENAI, pid="does-not-exist")


def test_a_setting_that_is_not_a_number_is_refused(store):
    with pytest.raises(settings.SettingsError, match="temperature must be a number"):
        settings.update_settings({"temperature": "warm"})


def test_deleting_the_embedding_provider_clears_the_choice(store):
    other = settings.save_provider(OPENAI)
    settings.update_settings({"embed_provider_id": other["id"]})
    settings.delete_provider(other["id"])
    assert settings.get_settings()["embed_provider_id"] == ""


def test_with_no_provider_at_all_the_default_is_a_clear_error_and_a_restart_seeds_one(store):
    db.execute("DELETE FROM providers")
    with pytest.raises(settings.SettingsError, match="No AI provider configured"):
        settings.default_provider()
    settings.seed()
    assert settings.default_provider()["kind"] == "mock"


def test_first_start_also_creates_the_provider_named_in_the_environment(store, monkeypatch):
    db.execute("DELETE FROM providers")
    for name, value in (("LLM_PROVIDER", "openai"), ("LLM_MODEL", "gpt-x"), ("LLM_BASE_URL", "https://llm.example.com/v1"), ("LLM_API_KEY", "sk-env")):
        monkeypatch.setattr(config, name, value)
    settings.seed()
    default = settings.default_provider()
    assert default["name"] == "Default (openai)" and default["api_key"] == "sk-env", "the environment's provider is the default and its key is stored encrypted"
