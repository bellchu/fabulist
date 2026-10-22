"""The AI is told which business it is writing requirements and stories for: a setting, not a sentence about medical distribution fixed in the code."""
from app import pipeline, settings


def _capture(monkeypatch):
    seen = []

    def fake(label, profile, opts, system, prompt, usage):
        seen.append(system)
        return []
    monkeypatch.setattr(pipeline, "_ask_at", fake)
    return seen


def _extract(opts):
    pipeline.extract_requirements([{"id": "d", "filename": "b.md", "text": "# A\nThe system shall do a thing for the buyer.\n"}], "generic",
                                  {"kind": "openai", "name": "x", "model": "m", "base_url": "http://x", "api_key": "k"}, dict(settings.DEFAULTS, **opts), lambda m: None, {"in": 0, "out": 0})


def test_the_default_keeps_todays_wording(client, monkeypatch):
    seen = _capture(monkeypatch)
    _extract({})
    assert seen and "medical product distribution" in seen[0]


def test_a_configured_domain_replaces_it_in_the_prompts(client, monkeypatch):
    seen = _capture(monkeypatch)
    _extract({"domain": "retail banking"})
    assert "retail banking" in seen[0] and "medical" not in seen[0] and "{domain}" not in seen[0]


def test_the_setting_is_validated_and_stored(client):
    r = client.put("/api/admin/settings", json={"domain": "  retail\nbanking\r\n  and insurance " + "x" * 300})
    assert r.status_code == 200
    saved = r.json()["values"]["domain"]
    assert "\n" not in saved and "\r" not in saved and saved.startswith("retail banking and insurance") and len(saved) <= 120
    assert client.put("/api/admin/settings", json={"domain": ""}).json()["values"]["domain"] == "medical product distribution", "empty goes back to the default"
    assert client.get("/api/admin/settings").json()["defaults"]["domain"] == "medical product distribution"
