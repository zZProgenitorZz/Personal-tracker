"""Sunny's conversation: what was said, the proposals waiting for Confirm, and a key or
credit problem, if any. All in the app's memory (one user, local), not in the
event store: only what you run with Confirm becomes an ordinary event.

The lock only protects the state below; during the (slow) API call it is free, so
Confirm, Cancel and New chat keep working while Sunny thinks. If you press New chat during a
call, the answer ends up in the new conversation.
"""
import logging
import threading
import uuid
from dataclasses import dataclass, field

from ..domain import DomainError
from .agent import Agent, SunnyProblem, make_client
from .commands import AssistantCommandHandler
from .tools import PendingAction, TrackerData

log = logging.getLogger("progen.assistant")


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
    _client: object = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

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

    # ---- A message ----

    def send(self, text: str, retry: bool) -> list[dict]:
        """A message from Progen; returns the new entries. With retry (Try again) the message
        is already in the conversation."""
        with self._lock:
            history = list(self.history)
        reply = Agent(self.client, self.data, self.settings.settings).ask(text, history)  # outside the lock
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
            if reply.actions:
                proposal = Proposal(uuid.uuid4().hex[:12], reply.actions)
                self.proposals[proposal.id] = proposal
                new.append({"kind": "card", "proposal": proposal})
                remembered += f"\n[Proposed, waiting for Progen's Confirm: {labels(reply.actions)}]"
            self.entries.extend(new)
            self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": remembered}]
            return new

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
