from fastapi.testclient import TestClient

from app.main import create_app


def make_client() -> TestClient:
    return TestClient(create_app(":memory:"))


def add_series(client: TestClient, title: str = "Solo Leveling"):
    return client.post(
        "/ui/series",
        data={"title": title, "kind": "manhwa", "source": "asura", "start_chapter": "1"},
    )


def series_id(client: TestClient) -> str:
    [entry] = client.get("/reading/library").json()
    return entry["series_id"]


def test_pages_render():
    client = make_client()
    add_series(client)
    for page in ["dashboard", "library", "library/grid", "progress", "settings"]:
        response = client.get(f"/ui/{page}")
        assert response.status_code == 200, page
        assert "text/html" in response.headers["content-type"]


def test_adding_series_shows_toast_and_announces_change():
    client = make_client()
    response = add_series(client)
    assert response.headers["HX-Retarget"] == "#toasts"
    assert response.headers["HX-Trigger"] == "reading-changed"
    assert "Solo Leveling" in client.get("/ui/dashboard").text


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
    response = client.post(f"/ui/series/{series_id(client)}/progress", data={"chapter": "58"})
    assert response.headers["HX-Trigger"] == "reading-changed"
    assert client.get("/reading/library").json()[0]["current_chapter"] == 58


def test_library_grid_filters_by_status_and_kind():
    client = make_client()
    add_series(client)
    client.post(f"/ui/series/{series_id(client)}/status", data={"status": "completed"})

    assert "Solo Leveling" in client.get("/ui/library/grid?status=completed").text
    assert "Solo Leveling" not in client.get("/ui/library/grid?status=reading").text
    assert "Solo Leveling" not in client.get("/ui/library/grid?kind=novel").text
    assert "Solo Leveling" in client.get("/ui/library/grid?q=solo").text


def test_unknown_series_gives_error_toast():
    client = make_client()
    response = client.post("/ui/series/bestaat-niet/progress", data={"chapter": "5"})
    assert "toast-error" in response.text


def test_removing_series_via_web():
    client = make_client()
    add_series(client)
    response = client.delete(f"/ui/series/{series_id(client)}")
    assert response.headers["HX-Trigger"] == "reading-changed"
    assert "Solo Leveling" not in client.get("/ui/library/grid").text
    assert "toast-error" not in add_series(client).text  # na verwijderen mag hij terug
