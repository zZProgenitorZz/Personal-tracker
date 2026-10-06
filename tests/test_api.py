import os

from fastapi.testclient import TestClient

from app.main import create_app


def make_client() -> TestClient:
    return TestClient(create_app(":memory:"))


def start_solo_leveling(client: TestClient) -> str:
    response = client.post(
        "/reading/series",
        json={"title": "Solo Leveling", "kind": "manhwa", "source": "asura", "start_chapter": 1},
    )
    assert response.status_code == 201
    return response.json()["series_id"]


def test_start_and_log_progress():
    client = make_client()
    series_id = start_solo_leveling(client)

    response = client.post(f"/reading/series/{series_id}/progress", json={"chapter": 57})
    assert response.json()["current_chapter"] == 57

    current = client.get("/reading/current").json()
    assert [s["title"] for s in current] == ["Solo Leveling"]


def test_duplicate_title_gives_400():
    client = make_client()
    start_solo_leveling(client)
    response = client.post(
        "/reading/series",
        json={"title": "solo leveling", "kind": "manhwa", "source": "asura"},
    )
    assert response.status_code == 400


def test_unknown_series_gives_404():
    client = make_client()
    response = client.post("/reading/series/bestaat-niet/progress", json={"chapter": 5})
    assert response.status_code == 404


def test_invalid_kind_gives_422():
    client = make_client()
    response = client.post(
        "/reading/series",
        json={"title": "Iets", "kind": "comic", "source": "ergens"},
    )
    assert response.status_code == 422

def test_homepage_is_served():
    client = make_client()
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

def test_remove_series():
    client = make_client()
    series_id = start_solo_leveling(client)
    assert client.delete(f"/reading/series/{series_id}").status_code == 204
    assert client.get("/reading/library").json() == []
    assert client.delete(f"/reading/series/{series_id}").status_code == 404


def test_settings_are_read_from_env_file(tmp_path, monkeypatch):
    # Eerst zetten en dan weghalen, zodat monkeypatch de oude toestand na de test terugzet.
    monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", "tijdelijk")
    monkeypatch.delenv("GOOGLE_BOOKS_API_KEY")
    env_file = tmp_path / ".env"
    env_file.write_text("GOOGLE_BOOKS_API_KEY=uit-bestand\n", encoding="utf-8")
    create_app(":memory:", env_file=env_file)
    assert os.environ["GOOGLE_BOOKS_API_KEY"] == "uit-bestand"


def test_real_environment_wins_over_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", "uit-terminal")
    env_file = tmp_path / ".env"
    env_file.write_text("GOOGLE_BOOKS_API_KEY=uit-bestand\n", encoding="utf-8")
    create_app(":memory:", env_file=env_file)
    assert os.environ["GOOGLE_BOOKS_API_KEY"] == "uit-terminal"
