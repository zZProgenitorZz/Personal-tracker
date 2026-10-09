"""Progen automatisch starten bij aanmelden in Windows (aan/uit in Settings).

Gebruikt de Run-sleutel van je eigen gebruiker (HKCU), dus geen beheerdersrechten
nodig. De waarde start launch.pyw met --background: de server en het icoon in het
systeemvak, zonder venster. Het register zit achter een klein object, zodat tests
een nep-register kunnen gebruiken.
"""
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Progen"
ROOT = Path(__file__).resolve().parent.parent


def launcher_command(root: Path = ROOT) -> str:
    pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    return f'"{pythonw}" "{root / "launch.pyw"}" --background'


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
        pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
        registry = WindowsRunKey() if sys.platform == "win32" and pythonw.exists() else None
        return cls(registry, launcher_command())

    @property
    def available(self) -> bool:
        return self._registry is not None

    @property
    def enabled(self) -> bool:
        # Een oude waarde (bv. na het verplaatsen van de projectmap) telt als uit.
        return self.available and self._registry.get(RUN_VALUE) == self.command

    def enable(self) -> None:
        if not self.available:
            raise RuntimeError("Starting with Windows is only available on Windows, with a .venv")
        self._registry.set(RUN_VALUE, self.command)

    def disable(self) -> None:
        if self.available:
            self._registry.delete(RUN_VALUE)
