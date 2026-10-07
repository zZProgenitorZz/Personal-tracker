from fastapi.testclient import TestClient

from app.main import create_app


def make_client() -> TestClient:
    return TestClient(create_app(":memory:"))


def add_series(client: TestClient, title: str = "Solo Leveling"):
    return client.post(
        "/ui/reading/series",
        data={"title": title, "kind": "manhwa", "source": "asura", "start_chapter": "1"},
    )


def series_id(client: TestClient) -> str:
    [entry] = client.get("/reading/library").json()
    return entry["series_id"]


def test_pages_render():
    client = make_client()
    add_series(client)
    for page in ["reading/dashboard", "reading/library", "reading/library/grid", "reading/progress", "settings"]:
        response = client.get(f"/ui/{page}")
        assert response.status_code == 200, page
        assert "text/html" in response.headers["content-type"]


def test_adding_series_shows_toast_and_announces_change():
    client = make_client()
    response = add_series(client)
    assert response.headers["HX-Retarget"] == "#toasts"
    assert response.headers["HX-Trigger"] == "reading-changed"
    assert "Solo Leveling" in client.get("/ui/reading/dashboard").text


def test_domain_error_becomes_error_toast_without_change():
    client = make_client()
    add_series(client)
    response = add_series(client, "solo leveling")
    assert response.status_code == 200
    assert "toast-error" in response.text
    assert "HX-Trigger" not in response.headers


def test_logging_progress_via_form():
    client = make_client()
    add_series(client)
    response = client.post(f"/ui/reading/series/{series_id(client)}/progress", data={"chapter": "58"})
    assert response.headers["HX-Trigger"] == "reading-changed"
    assert client.get("/reading/library").json()[0]["current_chapter"] == 58


def test_library_grid_filters_by_status_and_kind():
    client = make_client()
    add_series(client)
    client.post(f"/ui/reading/series/{series_id(client)}/status", data={"status": "completed"})

    assert "Solo Leveling" in client.get("/ui/reading/library/grid?status=completed").text
    assert "Solo Leveling" not in client.get("/ui/reading/library/grid?status=reading").text
    assert "Solo Leveling" not in client.get("/ui/reading/library/grid?kind=novel").text
    assert "Solo Leveling" in client.get("/ui/reading/library/grid?q=solo").text


def test_unknown_series_gives_error_toast():
    client = make_client()
    response = client.post("/ui/reading/series/bestaat-niet/progress", data={"chapter": "5"})
    assert "toast-error" in response.text


def test_removing_series_via_web():
    client = make_client()
    add_series(client)
    response = client.delete(f"/ui/reading/series/{series_id(client)}")
    assert response.headers["HX-Trigger"] == "reading-changed"
    assert "Solo Leveling" not in client.get("/ui/reading/library/grid").text
    assert "toast-error" not in add_series(client).text  # na verwijderen mag hij terug


def test_backup_and_restore_via_settings(tmp_path):
    client = TestClient(create_app(":memory:", backup_dir=tmp_path / "backups"))
    add_series(client, "Shadow Slave")

    response = client.post("/ui/backups")
    assert "toast-error" not in response.text
    assert "backups-changed" in response.headers["HX-Trigger"]
    [name] = [p.name for p in (tmp_path / "backups").iterdir()]

    add_series(client, "Later toegevoegd")
    page = client.get("/ui/settings").text
    assert name in page and "Back up now" in page

    response = client.post(f"/ui/backups/{name}/restore")
    assert "reading-changed" in response.headers["HX-Trigger"]
    assert [s["title"] for s in client.get("/reading/library").json()] == ["Shadow Slave"]


def test_restoring_unknown_backup_gives_error_toast(tmp_path):
    client = TestClient(create_app(":memory:", backup_dir=tmp_path / "backups"))
    assert "toast-error" in client.post("/ui/backups/bestaat-niet/restore").text


def test_add_series_with_status():
    client = make_client()
    response = client.post("/ui/reading/series", data={
        "title": "Lord of the Mysteries", "kind": "novel", "source": "x", "start_chapter": "1432", "status": "completed"})
    assert "Completed" in response.text
    assert client.get("/reading/library").json()[0]["status"] == "completed"


def test_add_series_without_status_is_reading():
    client = make_client()
    add_series(client)
    assert client.get("/reading/library").json()[0]["status"] == "reading"


def test_cards_in_the_library_do_not_inherit_the_filters():
    # Anders stuurt het statusmenu op een kaart het filter "All" (status="") mee.
    client = make_client()
    add_series(client)
    grid = client.get("/ui/reading/library/grid").text
    assert 'id="library-grid"' in grid and 'hx-disinherit="*"' in grid.split(">", 1)[0]


def test_browser_always_checks_for_a_newer_page_and_scripts():
    # Anders blijft de browser na een update oude index.html/trackly.js gebruiken.
    client = make_client()
    for path in ["/", "/static/trackly.js", "/static/trackly.css", "/ui/reading/dashboard"]:
        assert client.get(path).headers.get("cache-control") == "no-cache", path


# ---- Genres ----

def add_with_genres(client, title="Solo Leveling", genres=("Action", "Fantasy"), kind="manhwa"):
    return client.post("/ui/reading/series", data={"title": title, "kind": kind, "source": "x", "start_chapter": "1",
                                           "genres": list(genres)})


def test_add_series_with_genres():
    client = make_client()
    add_with_genres(client)
    assert client.get("/reading/library").json()[0]["genres"] == ["Action", "Fantasy"]
    assert "Action · Fantasy" in client.get("/ui/reading/library/grid").text


def test_add_form_lists_every_genre():
    from app.reading.genres import GENRES
    page = make_client().get("/ui/reading/add-form").text
    assert all(f'value="{g}"' in page for g in GENRES)


def test_edit_genres_later():
    client = make_client()
    add_with_genres(client)
    sid = series_id(client)

    form = client.get(f"/ui/reading/series/{sid}/genres").text
    assert 'value="Action" checked' in form and 'value="Romance">' in form

    response = client.post(f"/ui/reading/series/{sid}/genres", data={"genres": ["Romance"]})
    assert response.headers["HX-Trigger"] == "reading-changed"
    assert client.get("/reading/library").json()[0]["genres"] == ["Romance"]


def test_clearing_all_genres():
    client = make_client()
    add_with_genres(client)
    client.post(f"/ui/reading/series/{series_id(client)}/genres", data={})
    assert client.get("/reading/library").json()[0]["genres"] == []


def test_unknown_genre_gives_error_toast():
    client = make_client()
    add_series(client)
    response = client.post(f"/ui/reading/series/{series_id(client)}/genres", data={"genres": ["Ninja"]})
    assert "toast-error" in response.text


def test_library_filters_by_genre():
    client = make_client()
    add_with_genres(client, "Solo Leveling", ["Action"])
    add_with_genres(client, "True Beauty", ["Romance"])
    grid = client.get("/ui/reading/library/grid", params={"genre": "Romance"}).text
    assert "True Beauty" in grid and "Solo Leveling" not in grid


def test_progress_shows_genres():
    client = make_client()
    add_with_genres(client, "Solo Leveling", ["Action", "Fantasy"])
    add_with_genres(client, "Omniscient Reader", ["Action"])
    page = client.get("/ui/reading/progress").text
    assert "Genres" in page and "Action" in page
