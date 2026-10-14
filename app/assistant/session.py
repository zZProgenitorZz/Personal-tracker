"""Sunny's conversation: what was said, the proposals waiting for Confirm, and a key or
credit problem, if any. All in the app's memory (one user, local), not in the
event store: only what you run with Confirm becomes an ordinary event.

Chapter updates are the exception (trust stage 2, Settings › Sunny, on by default): a small step
forward is saved right away with an Undo button, either straight from what Progen said (quick_log,
no API call) or when it is the only change Claude proposes. Undo works once, until the next log.

The lock only protects the state below; during the (slow) API call it is free, so
Confirm, Cancel and New chat keep working while Sunny thinks. If you press New chat during a
call, the answer ends up in the new conversation.
"""
import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator

from ..domain import DomainError
from ..reading.commands import LogProgress
from .agent import Agent, SunnyProblem, make_client
from .commands import AddAlias, AssistantCommandHandler
from .notices import Notice, visible
from .quick import QuickLog, quick_log
from .tools import PendingAction, TrackerData

log = logging.getLogger("progen.assistant")


# Sunny's line after a quick log: a few, taking turns, so it doesn't sound like a machine.
QUICK_LINES = ["{title}, chapter {n}. Say undo if I misheard.", "{n} it is. {title}. Undo's right there.",
               "Logged. {title}, {n}."]
READING_CHANGED = "reading-changed"


@dataclass
class Undo:
    """The last chapter saved right away: what Undo sets back."""
    id: str
    series_id: str
    title: str
    old: float
    new: float
    heard: str | None  # the title as heard (quick log): a nickname for the right one if this was wrong


def labels(actions: list[PendingAction]) -> str:
    """ "Solo Leveling · chapter 119 → 120; New plan · gym · …" """
    return "; ".join(a.label for a in actions)


@dataclass
class Proposal:
    id: str
    actions: list[PendingAction]
    state: str = "pending"          # pending → confirmed / cancelled / failed
    problem: str | None = None

    @property
    def delete_names(self) -> list[str]:
        return [a.delete_name for a in self.actions if a.delete_name]


@dataclass
class Outcome:
    """What Confirm or Cancel produces, for the route: the card, Sunny's line, a toast and the events."""
    proposal: Proposal | None
    note: str | None = None
    toast: dict | None = None
    changed: list[str] = field(default_factory=list)


@dataclass
class Sunny:
    data: TrackerData
    settings: AssistantCommandHandler
    use_real_client: bool = True                       # in tests: never a real client
    history: list[dict] = field(default_factory=list)  # plain text, for the model
    entries: list[dict] = field(default_factory=list)  # what the panel shows
    proposals: dict[str, Proposal] = field(default_factory=dict)
    # A key or credit problem from the previous call: shown at the top when opening, until a call succeeds.
    standing_problem: SunnyProblem | None = None
    last_undo: Undo | None = None
    _client: object = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _undo_entry: dict | None = None    # the entry that shows the Undo button
    _undone: tuple | None = None       # (heard, chapter, wrong series_id) after Undo of a quick log
    _lines: int = 0

    def __post_init__(self):
        # Notices put away with "Not today": (key, local date). In memory only, like the proposals;
        # the tools see the same set through the data.
        self.dismissed = self.data.dismissed
        self.data.assistant = self.settings  # remember_alias goes through it after Confirm
        self.data.quick_saves = lambda: self.settings.settings().quick_saves

    @property
    def client(self):
        if self._client is None and self.use_real_client:
            self._client = make_client()
        return self._client

    @client.setter
    def client(self, value) -> None:
        self._client = value

    def reset(self) -> None:
        with self._lock:
            self.history.clear()
            self.entries.clear()
            self.proposals.clear()
            self.last_undo = self._undo_entry = self._undone = None

    # ---- Notices ----

    def notices(self) -> list[Notice]:
        return visible(self.data, self.data.now())

    def dismiss(self, key: str) -> None:
        """Hidden until tomorrow. No event: it is only about today."""
        with self._lock:
            self.dismissed.add((key, self.data.now().date()))

    # ---- A message ----

    def send(self, text: str, retry: bool) -> list[dict]:
        """A message from Progen; returns the new entries. With retry (Try again) the message
        is already in the conversation."""
        return next(value for kind, value in self.send_stream(text, retry) if kind == "entries")

    def send_stream(self, text: str, retry: bool, voice: bool = False,
                    alts: tuple[str, ...] = ()) -> Iterator[tuple[str, Any]]:
        """The same as send(), as it comes: the agent's ("text", delta) and ("status", line) events,
        and at the end ("entries", the new entries). With voice the message was spoken and the answer
        is read aloud: the model gets "(voice) ..." (see SOUL.md), the panel shows the plain text.
        `alts` are other things the recognizer thought it heard. A chapter log Sunny is sure about is
        saved straight away, without the API (and so also without a key)."""
        said = _said(text, voice, alts)
        if not retry and self.settings.settings().quick_saves:
            hit = quick_log([text, *alts], self.data.library, self.data.aliases)
            new = self._quick(text, said, hit) if hit else None
            if new is not None:
                yield "entries", new
                return
        if self.client is None:
            yield "entries", [{"kind": "unavailable"}]
            return
        with self._lock:
            history = list(self.history)
        reply = None
        for kind, value in Agent(self.client, self.data, self.settings.settings).ask_stream(said, history):
            if kind == "reply":
                reply = value
            else:
                yield kind, value  # outside the lock: Confirm, Cancel and New chat keep working
        yield "entries", self._remember(text, retry, reply, said)

    def _remember(self, text: str, retry: bool, reply, said: str) -> list[dict]:
        with self._lock:
            if not retry:
                self.entries.append({"kind": "user", "text": text})
            if reply.problem:
                # A problem with the API: a balloon of its own, and the message stays for Try again.
                if reply.problem.sticky:
                    self.standing_problem = reply.problem
                new = [{"kind": "problem", "problem": reply.problem, "retry_text": text}]
                self.entries.extend(new)
                return new
            self.standing_problem = None  # the API works again
            new = [{"kind": "sunny", "text": reply.text, "error": False}]
            remembered = reply.text
            saved = self._save_right_away(reply.actions)
            if saved is not None:  # one small chapter step: saved, with Undo instead of a card
                action, undo = saved
                new[0].update(undo=undo.id, changed=action.changed)
                self._undo_entry = new[0]
                remembered += f"\n[Saved right away: {action.label}; Progen can undo]"
            elif reply.actions:
                proposal = Proposal(uuid.uuid4().hex[:12], reply.actions)
                self.proposals[proposal.id] = proposal
                new.append({"kind": "card", "proposal": proposal})
                remembered += f"\n[Proposed, waiting for Progen's Confirm: {labels(reply.actions)}]"
            self.entries.extend(new)
            self.history += [{"role": "user", "content": said}, {"role": "assistant", "content": remembered}]
            return new

    # ---- Chapters right away, with Undo ----

    def _quick(self, text: str, said: str, hit: QuickLog) -> list[dict] | None:
        """Saves a quick log (no API call); None if the reading domain refuses it (then Claude takes over)."""
        with self._lock:
            try:
                self.data.reading.handle(LogProgress(hit.series_id, hit.new))
            except DomainError:
                return None
            undo = self._remember_undo(hit.series_id, hit.title, hit.old, hit.new, hit.heard)
            line = QUICK_LINES[self._lines % len(QUICK_LINES)].format(title=hit.title, n=f"{hit.new:g}")
            self._lines += 1
            new = [{"kind": "sunny", "text": line, "error": False, "undo": undo.id, "changed": READING_CHANGED}]
            self._undo_entry = new[0]
            self.entries += [{"kind": "user", "text": text}, *new]
            self.history += [{"role": "user", "content": said}, {"role": "assistant", "content": (
                f"{line}\n[Quick log: {hit.title} {hit.old:g} → {hit.new:g}, saved; Progen can undo]")}]
            return new

    def _save_right_away(self, actions: list[PendingAction]) -> tuple[PendingAction, Undo] | None:
        """Claude's only action is a small chapter step forward: save it now (the lock is held)."""
        if len(actions) != 1 or not actions[0].quick or not self.settings.settings().quick_saves:
            return None
        action = actions[0]
        entry = self.data.library.get(action.command.series_id)
        if entry is None:
            return None
        old = entry.current_chapter
        try:
            self.data.reading.handle(action.command)
        except DomainError:
            return None  # then the card, as usual
        return action, self._remember_undo(entry.series_id, entry.title, old, action.command.chapter, None)

    def _remember_undo(self, series_id: str, title: str, old: float, new: float, heard: str | None) -> Undo:
        """The new Undo replaces the old one (its button goes away); also learns a nickname: after Undo of
        a quick log, the same chapter for another series means the title was misheard."""
        if self._undo_entry is not None:
            self._undo_entry["undo"] = None
        self._learn(series_id, new)
        self.last_undo = Undo(uuid.uuid4().hex[:12], series_id, title, old, new, heard)
        return self.last_undo

    def undo(self, undo_id: str) -> str | None:
        """Sets the chapter back (an ordinary LogProgress: the same day counts as taking a typo back).
        Once; None when there is nothing (any more) to undo."""
        with self._lock:
            undo = self.last_undo
            if undo is None or undo.id != undo_id:
                return None
            self.last_undo = None
            if self._undo_entry is not None:
                self._undo_entry["undo"] = None
                self._undo_entry = None
            try:
                self.data.reading.handle(LogProgress(undo.series_id, undo.old))
            except DomainError as exc:
                return f"Couldn't undo that: {exc}"
            if undo.heard:
                self._undone = (undo.heard, undo.new, undo.series_id)
            note = f"Undone. Back to {undo.old:g}."
            self.entries.append({"kind": "sunny", "text": note, "error": False})
            self.history.append({"role": "user", "content": f"[Progen clicked Undo: {undo.title} back to "
                                                            f"chapter {undo.old:g}, not {undo.new:g}]"})
            return note

    # ---- Confirm and Cancel ----

    def confirm(self, proposal_id: str) -> Outcome:
        """Runs the proposed actions through the ordinary command handlers (local, no API needed).
        After that the proposal is never pending again: running it twice can't happen."""
        with self._lock:
            proposal = self.proposals.get(proposal_id)
            if proposal is None or proposal.state != "pending":
                return Outcome(proposal)
            proposal.state = "failed"  # off pending right away, whatever happens below
            done, failed = [], []
            for action in proposal.actions:
                try:
                    self.data.handler(action.domain).handle(action.command)
                    done.append(action)
                    if isinstance(action.command, LogProgress):
                        self._learn(action.command.series_id, action.command.chapter)
                except DomainError as exc:
                    failed.append(f"{action.label}: {exc}")
                except Exception:  # never let the app fall over; do log it
                    log.exception("Sunny: confirming %r failed", action.label)
                    failed.append(f"{action.label}: unexpected error")
            if failed:
                proposal.problem = " · ".join(failed)
                note = f"Not everything went through: {proposal.problem}" + (f". Saved: {labels(done)}." if done else ".")
                toast = {"message": "Sunny couldn't save everything", "error": True}
            else:
                proposal.state = "confirmed"
                note = "Done."
                toast = {"message": f"Saved · {labels(done)}", "error": False}
            self.entries.append({"kind": "sunny", "text": note, "error": bool(failed)})
            self.history.append({"role": "user", "content": f"[Progen clicked Confirm. Saved: {labels(done) or 'nothing'}"
                                                            + (f"; failed: {proposal.problem}" if failed else "") + "]"})
            return Outcome(proposal, note, toast, sorted({a.changed for a in done}))

    def _learn(self, series_id: str, chapter: float) -> None:
        """After Undo of a quick log, the same chapter for another series means the title was misheard:
        remember what was heard as a nickname for the right one (an event, so it survives a backup)."""
        if self._undone is None:
            return
        wrong_heard, wrong_chapter, wrong_series = self._undone
        self._undone = None
        if wrong_chapter == chapter and wrong_series != series_id:
            try:
                self.settings.handle(AddAlias(wrong_heard, series_id))
            except DomainError:
                log.warning("Sunny: couldn't remember %r as a nickname", wrong_heard)

    def cancel(self, proposal_id: str) -> Outcome:
        with self._lock:
            proposal = self.proposals.get(proposal_id)
            if proposal is None or proposal.state != "pending":
                return Outcome(proposal)
            proposal.state = "cancelled"
            note = "Cancelled. What should be different?"
            self.entries.append({"kind": "sunny", "text": note, "error": False})
            self.history.append({"role": "user", "content": f"[Progen clicked Cancel: {labels(proposal.actions)}]"})
            return Outcome(proposal, note)


def _said(text: str, voice: bool, alts: tuple[str, ...]) -> str:
    """What the model gets: '(voice) ...', with what else the recognizer heard, kept short."""
    if not voice:
        return text
    if not alts:
        return f"(voice) {text}"
    also = " | ".join('"' + alt + '"' for alt in alts)
    return f"(voice; also heard: {also}) {text}"
