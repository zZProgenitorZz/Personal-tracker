"""Sunny's gesprek: wat er gezegd is, de voorstellen die op Confirm wachten, en een eventueel
key- of tegoedprobleem. Alles in het geheugen van de app (één gebruiker, lokaal), niet in de
event store: alleen wat je met Confirm uitvoert wordt een gewoon event.

De lock beschermt alleen de toestand hieronder; tijdens de (trage) API-aanroep is hij vrij, zodat
Confirm, Cancel en New chat gewoon werken terwijl Sunny nadenkt. Doe je New chat tijdens een
aanroep, dan komt het antwoord in het nieuwe gesprek terecht.
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
    """Wat Confirm of Cancel oplevert, voor de route: de kaart, Sunny's regel, een toast en de events."""
    proposal: Proposal | None
    note: str | None = None
    toast: dict | None = None
    changed: list[str] = field(default_factory=list)


@dataclass
class Sunny:
    data: TrackerData
    settings: AssistantCommandHandler
    use_real_client: bool = True                       # in tests: nooit een echte client
    history: list[dict] = field(default_factory=list)  # platte tekst, voor het model
    entries: list[dict] = field(default_factory=list)  # wat het paneel toont
    proposals: dict[str, Proposal] = field(default_factory=dict)
    # Een key- of tegoedprobleem van de vorige aanroep: staat bovenaan bij openen, tot een aanroep lukt.
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

    # ---- Een bericht ----

    def send(self, text: str, retry: bool) -> list[dict]:
        """Een bericht van Progen; geeft de nieuwe entries terug. Met retry (Try again) staat het
        bericht al in het gesprek."""
        with self._lock:
            history = list(self.history)
        reply = Agent(self.client, self.data, self.settings.settings).ask(text, history)  # buiten de lock
        with self._lock:
            if not retry:
                self.entries.append({"kind": "user", "text": text})
            if reply.problem:
                # Een probleem met de API: een eigen ballon, en het bericht blijft staan voor Try again.
                if reply.problem.sticky:
                    self.standing_problem = reply.problem
                new = [{"kind": "problem", "problem": reply.problem, "retry_text": text}]
                self.entries.extend(new)
                return new
            self.standing_problem = None  # de API doet het weer
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

    # ---- Confirm en Cancel ----

    def confirm(self, proposal_id: str) -> Outcome:
        """Voert de voorgestelde acties uit via de gewone command handlers (lokaal, geen API nodig).
        Daarna staat het voorstel nooit meer op pending: dubbel uitvoeren kan niet."""
        with self._lock:
            proposal = self.proposals.get(proposal_id)
            if proposal is None or proposal.state != "pending":
                return Outcome(proposal)
            proposal.state = "failed"  # meteen weg van pending, wat er hieronder ook gebeurt
            done, failed = [], []
            for action in proposal.actions:
                try:
                    self.data.handler(action.domain).handle(action.command)
                    done.append(action)
                except DomainError as exc:
                    failed.append(f"{action.label}: {exc}")
                except Exception:  # nooit de app laten vallen; wel loggen
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
