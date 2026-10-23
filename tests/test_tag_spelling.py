"""One spelling per compliance tag: saving an item with 'sox' in a project that already says 'SOX' keeps 'SOX'."""


def test_a_saved_item_reuses_the_spelling_the_project_already_has(client):
    pid = client.post("/api/projects", json={"name": "Spelling", "platform": "generic"}).json()["id"]
    post = lambda path, body: client.post("/api/projects/%s/%s" % (pid, path), json=body).json()  # noqa: E731
    post("requirements", {"statement": "The system shall log every payment attempt.", "compliance_tags": ["SOX"]})
    second = post("requirements", {"statement": "The system shall mask card numbers in reports.", "compliance_tags": ["sox", "SOX", "New-Tag"]})
    assert second["compliance_tags"] == ["New-Tag", "SOX"], "known spelling reused; a new tag keeps what was typed; duplicates by case collapse"
    story = post("stories", {"title": "Audit payments", "compliance_tags": ["Sox"]})
    assert story["compliance_tags"] == ["SOX"], "stories share the spelling with requirements"
