from app import pipeline, taxonomy


def test_defaults_seeded_and_used(client):
    t = client.get("/api/admin/taxonomy").json()
    assert len(t["classes"]) == len(taxonomy.DEFAULT_CLASSES) and t["attribute_rules"]
    assert "Biologics and vaccines" in client.get("/api/meta").json()["product_classes"]


def test_custom_class_drives_matching_and_variants(client):
    c = client.post("/api/admin/taxonomy/classes", json={"name": "Radiopharmaceuticals", "attributes": "radioactive; short_half_life",
                                                         "keywords": "Radiopharm, isotope, Tc-99m"}).json()
    assert c["attributes"] == ["radioactive", "short_half_life"] and c["keywords"] == ["isotope", "radiopharm", "tc-99m"]
    client.put("/api/admin/taxonomy/rules", json={"rules": {"short_half_life": "Orders are scheduled so product arrives before decay limits."}})
    assert taxonomy.match_classes("Ship Tc-99m generators overnight") == ["Radiopharmaceuticals"]
    assert taxonomy.variant_criteria(["Radiopharmaceuticals"]) == [
        {"scope": "Radiopharmaceuticals", "criteria": "Orders are scheduled so product arrives before decay limits."}]
    assert "Radiopharmaceuticals (radioactive, short_half_life)" in pipeline._platform_context("generic")
    assert client.post("/api/admin/taxonomy/classes", json={"name": "radiopharmaceuticals"}).status_code == 409
    assert client.post("/api/admin/taxonomy/classes", json={"name": "X", "attributes": "Bad-Attr!"}).status_code == 400
    assert client.put("/api/admin/taxonomy/classes/%s" % c["id"], json={"name": "Radiopharma", "keywords": "isotope"}).json()["name"] == "Radiopharma"
    assert client.delete("/api/admin/taxonomy/classes/%s" % c["id"]).status_code == 204
    assert taxonomy.match_classes("isotope shipment") == []


def test_csv_import_merge_replace_and_export(client):
    csv_text = "name,attributes,keywords,description\nOphthalmic devices,udi_tracked;sterile,lens;ophthalmic,Eye care\nBiologics and vaccines,cold_chain,vaccine;biologic,\n"
    r = client.post("/api/admin/taxonomy/import", json={"csv": csv_text}).json()
    assert r == {"created": 1, "updated": 1, "removed": 0}
    names = [c["name"] for c in client.get("/api/admin/taxonomy").json()["classes"]]
    assert "Ophthalmic devices" in names and len(names) == len(taxonomy.DEFAULT_CLASSES) + 1
    r = client.post("/api/admin/taxonomy/import", json={"csv": csv_text, "mode": "replace"}).json()
    assert r["removed"] == len(taxonomy.DEFAULT_CLASSES) - 1
    assert client.post("/api/admin/taxonomy/import", json={"csv": "foo,bar\n1,2\n"}).status_code == 400
    assert "line 2" in client.post("/api/admin/taxonomy/import", json={"csv": "name,attributes\nX,BAD!\n"}).json()["detail"]
    exported = client.get("/api/admin/taxonomy.csv").text
    assert exported.splitlines()[0] == "name,attributes,keywords,description" and "Ophthalmic devices,udi_tracked; sterile" in exported


def test_invalidate_during_a_load_is_not_overwritten_by_the_stale_result(client, monkeypatch):
    from app import db
    taxonomy.invalidate()
    real_rows, raced = db.rows, []

    def racing_rows(sql, *args, **kwargs):
        out = real_rows(sql, *args, **kwargs)
        if "FROM product_classes" in sql and not raced:
            raced.append(1)  # a writer commits and invalidates after this reader's SELECT
            client.post("/api/admin/taxonomy/classes", json={"name": "Raced class", "keywords": "raced"})
        return out

    monkeypatch.setattr(db, "rows", racing_rows)
    taxonomy.classes()
    monkeypatch.setattr(db, "rows", real_rows)
    assert "Raced class" in [c["name"] for c in taxonomy.classes()]


def test_invalidate_works_on_a_freshly_booted_machine(client, monkeypatch):
    # time.monotonic() counts from boot: with under 30 s of uptime, "stale since 0.0" used to look fresh,
    # so an invalidated cache kept serving old data (seen as flaky tests right after the Docker VM restarted).
    import types
    monkeypatch.setattr(taxonomy, "time", types.SimpleNamespace(monotonic=lambda: 5.0))
    taxonomy.invalidate()
    taxonomy.classes()  # warm the cache
    client.post("/api/admin/taxonomy/classes", json={"name": "Fresh boot class", "keywords": "freshboot"})  # invalidates
    assert "Fresh boot class" in [c["name"] for c in taxonomy.classes()]
