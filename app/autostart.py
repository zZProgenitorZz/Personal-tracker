"""Starts Progen automatically when you sign in to Windows (on/off in Settings).

Uses the Run key of your own user (HKCU), so no admin rights are needed. The value
starts launch.pyw with --background: the server and the tray icon, without a window.
The registry sits behind a small object, so tests can use a fake registry.
"""
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Progen"
ROOT = Path(__file__).resolve().parent.parent


def pythonw_path(root: Path = ROOT) -> Path:
    """The venv's pythonw.exe: Python without a console window."""
    return root / ".venv" / "Scripts" / "pythonw.exe"


def launcher_command(root: Path = ROOT) -> str:
    return f'"{pythonw_path(root)}" "{root / "launch.pyw"}" --background'


class WindowsRunKey:
    """HKCU\\...\\Run via winreg."""

    def __init__(self):
        import winreg
        self._winreg = winreg

    def _key(self, access):
        return self._winreg.CreateKeyEx(self._winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access)

    def get(self, name: str) -> str | None:
        try:
            with self._key(self._winreg.KEY_READ) as key:
                return self._winreg.QueryValueEx(key, name)[0]
        except FileNotFoundError:
            return None

    def set(self, name: str, value: str) -> None:
        with self._key(self._winreg.KEY_SET_VALUE) as key:
            self._winreg.SetValueEx(key, name, 0, self._winreg.REG_SZ, value)

    def delete(self, name: str) -> None:
        try:
            with self._key(self._winreg.KEY_SET_VALUE) as key:
                self._winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass


class Autostart:
    def __init__(self, registry, command: str):
        self._registry = registry
        self.command = command

    @classmethod
    def for_this_computer(cls) -> "Autostart":
        registry = WindowsRunKey() if sys.platform == "win32" and pythonw_path().exists() else None
        return cls(registry, launcher_command())

    @property
    def available(self) -> bool:
        return self._registry is not None

    @property
    def enabled(self) -> bool:
        # An old value (e.g. after moving the project folder) counts as off.
        return self.available and self._registry.get(RUN_VALUE) == self.command

    def enable(self) -> None:
        if not self.available:
            raise RuntimeError("Starting with Windows is only available on Windows, with a .venv")
        self._registry.set(RUN_VALUE, self.command)

    def disable(self) -> None:
        if self.available:
            self._registry.delete(RUN_VALUE)
