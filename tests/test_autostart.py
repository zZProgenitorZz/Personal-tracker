"""Automatisch starten bij aanmelden in Windows, aan en uit te zetten in Settings.
Met een nep-register: de echte Windows-instellingen worden niet aangeraakt."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.autostart import RUN_VALUE, Autostart, launcher_command
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent


class FakeRegistry:
    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


@pytest.fixture
def registry():
    return FakeRegistry()


def test_command_starts_the_launcher_in_the_background_without_a_console():
    command = launcher_command(ROOT)
    assert command == f'"{ROOT / ".venv" / "Scripts" / "pythonw.exe"}" "{ROOT / "launch.pyw"}" --background'


def test_enable_and_disable(registry):
    autostart = Autostart(registry, "cmd")
    assert not autostart.enabled
    autostart.enable()
    assert registry.values == {RUN_VALUE: "cmd"} and autostart.enabled
    autostart.disable()
    assert registry.values == {} and not autostart.enabled


def test_an_old_command_counts_as_off_until_enabled_again(registry):
    # Na het verplaatsen van de projectmap wijst de oude waarde naar een pad dat niet meer bestaat.
    registry.values[RUN_VALUE] = '"C:\\oud\\pythonw.exe" "C:\\oud\\launch.pyw" --background'
    autostart = Autostart(registry, "nieuw")
    assert not autostart.enabled
    autostart.enable()
    assert registry.values[RUN_VALUE] == "nieuw"


def test_not_available_without_registry():
    autostart = Autostart(None, "cmd")
    assert not autostart.available and not autostart.enabled
    with pytest.raises(RuntimeError):
        autostart.enable()


# ---- In Settings ----

@pytest.fixture
def client(registry):
    app = create_app(":memory:")
    app.state.autostart = Autostart(registry, "cmd")
    return TestClient(app)


def test_settings_shows_the_switch(client):
    assert 'hx-get="/ui/autostart"' in client.get("/ui/settings").text
    panel = client.get("/ui/autostart").text
    assert 'name="enabled"' in panel and "checked" not in panel


def test_switch_on_and_off(client, registry):
    response = client.post("/ui/autostart", data={"enabled": "on"}, headers={"HX-Request": "true"})
    assert registry.values == {RUN_VALUE: "cmd"} and "checked" in client.get("/ui/autostart").text
    assert "autostart-changed" in response.headers["HX-Trigger"]
    client.post("/ui/autostart", data={}, headers={"HX-Request": "true"})
    assert registry.values == {}


def test_switch_only_from_the_app_itself(client, registry):
    # Zonder HX-Request (een andere website) verandert er niets.
    assert client.post("/ui/autostart", data={"enabled": "on"}).status_code == 403
    assert registry.values == {}


def test_settings_explains_when_not_available():
    app = create_app(":memory:")
    app.state.autostart = Autostart(None, "cmd")
    panel = TestClient(app).get("/ui/autostart").text
    assert "only on Windows" in panel and 'name="enabled"' not in panel
