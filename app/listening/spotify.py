"""Live connection with Spotify: an automation slice.

Periodically fetches the tracks you listened to last and sends exactly the same
command for each as the API (RecordPlay, source="api"). The domain logic doesn't
know Spotify exists.

- Signing in: Authorization Code flow with a `state` against CSRF.
- Tokens live in data/spotify_token.json, not in the event store (and so not
  in a backup either). The access token is refreshed automatically.
- Spotify returns at most the last 50 tracks; whatever you listened to before
  that and wasn't synced yet can no longer be fetched this way.
"""
import base64
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import httpx

from .. import clock as app_clock
from ..covers import CoverError, process_image
from ..domain import DomainError
from .commands import ListeningCommandHandler, RecordPlay
from .projections import RecentlyPlayedProjection

log = logging.getLogger("progen.listening")

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
RECENTLY_PLAYED_URL = "https://api.spotify.com/v1/me/player/recently-played"
PROFILE_URL = "https://api.spotify.com/v1/me"
AVATAR_SIZE = 256                    # profile photo: square, in pixels
PROFILE_REFRESH_SECONDS = 24 * 3600  # fetch the name and photo again at most once a day
SCOPE = "user-read-recently-played"
REDIRECT_URI = "http://127.0.0.1:8000/listening/spotify/callback"
CLIENT_ID_ENV, CLIENT_SECRET_ENV, REDIRECT_URI_ENV = "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REDIRECT_URI"
INTERVAL_ENV = "SYNC_INTERVAL_MINUTES"
DEFAULT_INTERVAL_MINUTES = 30
STATE_SECONDS = 10 * 60      # signing in at Spotify may take this long
REFRESH_MARGIN = 60          # refresh the access token if it expires within a minute
TIMEOUT = 15.0
DEFAULT_RETRY_AFTER = 60


class SpotifyError(Exception):
    """Something went wrong with Spotify. The message is meant for the user."""


class NotConnected(SpotifyError):
    pass


class RateLimited(SpotifyError):
    def __init__(self, retry_after: int):
        super().__init__(f"Spotify is busy; try again in {retry_after} seconds")
        self.retry_after = retry_after


def sync_interval_seconds() -> int:
    raw = os.environ.get(INTERVAL_ENV, "").strip()
    try:
        minutes = float(raw) if raw else DEFAULT_INTERVAL_MINUTES
        if minutes <= 0:
            raise ValueError
    except ValueError:
        log.warning("%s=%r is not a positive number; using %s minutes", INTERVAL_ENV, raw, DEFAULT_INTERVAL_MINUTES)
        minutes = DEFAULT_INTERVAL_MINUTES
    return round(minutes * 60)


class TokenFile:
    """The Spotify tokens in a separate JSON file (not in git, not in the event store)."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and data.get("refresh_token") else None

    def save(self, token: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(token, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class Spotify:
    """Signing in, managing tokens and fetching recently-played."""

    def __init__(self, http: httpx.Client, tokens: TokenFile, client_id: str, client_secret: str,
                 redirect_uri: str = REDIRECT_URI, clock=time.time):
        self._http = http
        self.tokens = tokens
        self._client_id = (client_id or "").strip()
        self._client_secret = (client_secret or "").strip()
        self.redirect_uri = redirect_uri
        self._clock = clock
        self._states: dict[str, float] = {}  # state -> expires at

    @classmethod
    def from_environment(cls, http: httpx.Client, tokens: TokenFile) -> "Spotify":
        return cls(http, tokens, os.environ.get(CLIENT_ID_ENV, ""), os.environ.get(CLIENT_SECRET_ENV, ""),
                   os.environ.get(REDIRECT_URI_ENV, "").strip() or REDIRECT_URI)

    @property
    def configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    @property
    def connected(self) -> bool:
        return self.tokens.load() is not None

    # ---- Signing in ----

    def authorize_url(self) -> str:
        if not self.configured:
            raise SpotifyError(f"Add {CLIENT_ID_ENV} and {CLIENT_SECRET_ENV} to .env and restart Progen")
        now = self._clock()
        self._states = {s: until for s, until in self._states.items() if until > now}
        state = secrets.token_urlsafe(24)
        self._states[state] = now + STATE_SECONDS
        return f"{AUTHORIZE_URL}?" + urlencode({
            "client_id": self._client_id, "response_type": "code", "redirect_uri": self.redirect_uri,
            "scope": SCOPE, "state": state,
        })

    def finish_authorization(self, code: str, state: str) -> None:
        until = self._states.pop(state, None)  # a state only works once
        if until is None or until < self._clock():
            raise SpotifyError("The login came back with an unknown or expired state. Try connecting again.")
        answer = self._token_request({"grant_type": "authorization_code", "code": code,
                                      "redirect_uri": self.redirect_uri})
        self.tokens.save(self._token_from(answer, previous_refresh=None))

    def disconnect(self) -> None:
        self.tokens.clear()

    # ---- Tokens ----

    def _token_request(self, form: dict) -> dict:
        basic = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
        try:
            response = self._http.post(TOKEN_URL, data=form, timeout=TIMEOUT,
                                       headers={"Authorization": f"Basic {basic}"})
        except httpx.HTTPError as exc:
            raise SpotifyError("Couldn't reach Spotify.") from exc
        if response.status_code in (400, 401) and form["grant_type"] == "refresh_token":
            self.tokens.clear()  # access revoked or expired: connect again
            raise SpotifyError("Spotify no longer accepts Progen's access; reconnect Spotify in Settings.")
        if response.status_code != 200:
            raise SpotifyError(f"Spotify refused the login (HTTP {response.status_code}).")
        try:
            return response.json()
        except ValueError as exc:
            raise SpotifyError("Spotify sent an answer Progen doesn't understand.") from exc

    def _token_from(self, answer: dict, previous_refresh: str | None) -> dict:
        return {
            "access_token": answer["access_token"],
            # When refreshing, Spotify doesn't always send a new refresh token along.
            "refresh_token": answer.get("refresh_token") or previous_refresh,
            "expires_at": self._clock() + int(answer.get("expires_in", 3600)),
        }

    def _refresh(self, token: dict) -> dict:
        answer = self._token_request({"grant_type": "refresh_token", "refresh_token": token["refresh_token"]})
        token = self._token_from(answer, previous_refresh=token["refresh_token"])
        self.tokens.save(token)
        return token

    def _access_token(self, force_refresh: bool = False) -> str:
        token = self.tokens.load()
        if token is None:
            raise NotConnected("Spotify isn't connected.")
        if force_refresh or token.get("expires_at", 0) - REFRESH_MARGIN <= self._clock():
            token = self._refresh(token)
        return token["access_token"]

    # ---- Fetching ----

    def recently_played(self, after_ms: int | None) -> list[dict]:
        params = {"limit": 50}
        if after_ms is not None:
            params["after"] = after_ms
        response = self._authorized_get(RECENTLY_PLAYED_URL, params)
        if response.status_code == 429:
            retry = response.headers.get("Retry-After", "")
            raise RateLimited(int(retry) if retry.isdigit() else DEFAULT_RETRY_AFTER)
        if response.status_code != 200:
            raise SpotifyError(f"Spotify gave an error (HTTP {response.status_code}).")
        try:
            return response.json().get("items") or []
        except (ValueError, AttributeError) as exc:
            raise SpotifyError("Spotify sent an answer Progen doesn't understand.") from exc

    def profile(self) -> dict:
        """Your Spotify profile: display_name and images (your profile photo), among others."""
        response = self._authorized_get(PROFILE_URL, {})
        if response.status_code != 200:
            raise SpotifyError(f"Spotify didn't give the profile (HTTP {response.status_code}).")
        try:
            return response.json()
        except ValueError as exc:
            raise SpotifyError("Spotify sent a profile Progen doesn't understand.") from exc

    def _authorized_get(self, url: str, params: dict) -> httpx.Response:
        response = self._get(url, params, self._access_token())
        if response.status_code == 401:  # token just expired or revoked: refresh once
            response = self._get(url, params, self._access_token(force_refresh=True))
        return response

    def _get(self, url: str, params: dict, access_token: str) -> httpx.Response:
        try:
            return self._http.get(url, params=params, timeout=TIMEOUT,
                                  headers={"Authorization": f"Bearer {access_token}"})
        except httpx.HTTPError as exc:
            raise SpotifyError("Couldn't reach Spotify.") from exc


def play_from_item(item: dict) -> RecordPlay | None:
    """One item from recently-played as a command; None if there is no track in it."""
    track = (item or {}).get("track") or {}
    if not track.get("uri") or not item.get("played_at"):
        return None
    album = track.get("album") or {}
    return RecordPlay(
        played_at=datetime.fromisoformat(item["played_at"].replace("Z", "+00:00")),
        track_id=track["uri"],
        track=track.get("name") or "",
        artists=[a.get("name", "") for a in track.get("artists") or [] if a.get("name")],
        album=album.get("name") or "",
        album_id=album.get("id") or "",
        duration_ms=int(track.get("duration_ms") or 0),
        source="api",
    )


class ProfileStore:
    """Your Spotify name and profile photo, next to the tokens (not in the event store, not in a backup)."""

    def __init__(self, directory: Path):
        self.json_path = Path(directory) / "spotify_profile.json"
        self.avatar_path = Path(directory) / "spotify_avatar.webp"

    def load(self) -> dict | None:
        try:
            profile = json.loads(self.json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        profile["has_avatar"] = self.avatar_path.exists()
        return profile

    def save(self, profile: dict, avatar: bytes | None) -> None:
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        if avatar:
            self.avatar_path.write_bytes(avatar)
        else:
            self.avatar_path.unlink(missing_ok=True)
        self.json_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")

    def clear(self) -> None:
        self.json_path.unlink(missing_ok=True)
        self.avatar_path.unlink(missing_ok=True)


@dataclass(frozen=True)
class SyncResult:
    stored: int
    skipped: int
    at: datetime


class ListeningSync:
    """One sync: asks Spotify for plays after the last one we know and records them."""

    def __init__(self, spotify: Spotify, handler: ListeningCommandHandler, recent: RecentlyPlayedProjection,
                 status_file: Path, interval_seconds: int, clock=lambda: app_clock.utc_now(),
                 profiles: ProfileStore | None = None, download=None):
        self.spotify = spotify
        self.profiles = profiles
        self._download = download  # url -> bytes, with the same limits as covers
        self._handler = handler
        self._recent = recent
        self._status_file = Path(status_file)
        self.interval_seconds = interval_seconds
        self._clock = clock
        # The background sync, "Sync now" and the callback never run at the same time:
        # otherwise two runs could both store the same play.
        self._lock = threading.Lock()

    def run(self) -> SyncResult:
        with self._lock:
            return self._run()

    def _run(self) -> SyncResult:
        latest = self._recent.latest_played_at()
        after_ms = int(latest.timestamp() * 1000) if latest else None
        try:
            items = self.spotify.recently_played(after_ms)
        except SpotifyError as exc:
            self._remember(stored=0, skipped=0, error=str(exc))
            raise
        stored = skipped = 0
        for item in items:
            command = play_from_item(item)
            try:
                saved = command is not None and bool(self._handler.handle(command))
            except DomainError as exc:
                log.warning("Skipped a Spotify play: %s", exc)
                saved = False
            stored, skipped = (stored + 1, skipped) if saved else (stored, skipped + 1)
        result = SyncResult(stored, skipped, self._clock())
        self._remember(stored, skipped, error=None)
        self.refresh_profile()
        return result

    # ---- Profile (name and photo) ----

    def refresh_profile(self, force: bool = False) -> None:
        """Fetches the name and photo, at most once per PROFILE_REFRESH_SECONDS (unless force).
        Never raises: without a profile everything simply works."""
        if self.profiles is None or not self.spotify.connected:
            return
        current = self.profiles.load()
        if not force and current and self._clock().timestamp() - current.get("fetched_at", 0) < PROFILE_REFRESH_SECONDS:
            return
        try:
            me = self.spotify.profile()
        except SpotifyError as exc:
            log.warning("Couldn't fetch the Spotify profile: %s", exc)
            return
        images = sorted((i for i in me.get("images") or [] if i.get("url")),
                        key=lambda i: i.get("width") or 0, reverse=True)
        avatar = None
        if images and self._download:
            try:
                avatar = process_image(self._download(images[0]["url"]), size=(AVATAR_SIZE, AVATAR_SIZE))
            except CoverError as exc:
                log.warning("Couldn't use the Spotify profile photo: %s", exc)
        self.profiles.save({"display_name": me.get("display_name") or me.get("id") or "",
                            "fetched_at": self._clock().timestamp()}, avatar)

    def disconnect(self) -> None:
        self.spotify.disconnect()
        if self.profiles is not None:
            self.profiles.clear()

    def tick(self) -> float:
        """One round of the background task. Returns how many seconds until the next one.
        Never raises: the app must not crash because of this."""
        try:
            if not self.spotify.connected:
                return self.interval_seconds
            result = self.run()
            log.info("Spotify sync: %d new, %d already known", result.stored, result.skipped)
        except RateLimited as exc:
            log.warning("Spotify sync failed: rate limited, waiting %s s", exc.retry_after)
            return max(exc.retry_after, 1)
        except SpotifyError as exc:
            log.warning("Spotify sync failed: %s", exc)
        except Exception:
            log.exception("Spotify sync crashed")
        return self.interval_seconds

    # ---- When was the last sync? (survives a restart) ----

    def _remember(self, stored: int, skipped: int, error: str | None) -> None:
        status = {"last_sync_at": self._clock().isoformat(), "stored": stored, "skipped": skipped, "error": error}
        try:
            self._status_file.parent.mkdir(parents=True, exist_ok=True)
            self._status_file.write_text(json.dumps(status, indent=2), encoding="utf-8")
        except OSError:
            log.exception("Couldn't save the Spotify sync status")

    def status(self) -> dict:
        try:
            status = json.loads(self._status_file.read_text(encoding="utf-8"))
            status["last_sync_at"] = datetime.fromisoformat(status["last_sync_at"])
            return status
        except (OSError, ValueError, KeyError, TypeError):
            return {"last_sync_at": None, "stored": 0, "skipped": 0, "error": None}
