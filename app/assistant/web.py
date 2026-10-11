"""Sunny in de app: het chatpaneel (htmx) en Confirm/Cancel van voorgestelde acties.

Het gesprek en de voorstellen leven in het geheugen van de app (één gebruiker, lokaal), niet
in de event store: alleen wat je met Confirm uitvoert wordt een gewoon event. New chat wist
alles. Zonder ANTHROPIC_API_KEY toont het paneel één nette regel in plaats van een fout.
"""
import threading
import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, Form, Request

from ..domain import DomainError
from ..web import render
from .agent import Agent, make_client
from .tools import PendingAction, TrackerData


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
class Sunny:
    data: TrackerData
    use_real_client: bool = True                       # in tests: nooit een echte client
    history: list[dict] = field(default_factory=list)  # platte tekst, voor het model
    entries: list[dict] = field(default_factory=list)  # wat het paneel toont
    proposals: dict[str, Proposal] = field(default_factory=dict)
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


def create_assistant_router(sunny: Sunny) -> APIRouter:
    router = APIRouter(prefix="/ui/assistant", include_in_schema=False)

    def body(request: Request):
        return render(request, "_sunny_body.html", available=sunny.client is not None, entries=sunny.entries)

    @router.get("")
    def show(request: Request):
        return body(request)

    @router.post("/message")
    def message(request: Request, message: str = Form("")):
        text = message.strip()
        if sunny.client is None:
            return render(request, "_sunny_entries.html", entries=[{"kind": "unavailable"}])
        if not text:
            return render(request, "_sunny_entries.html", entries=[])
        with sunny._lock:
            reply = Agent(sunny.client, sunny.data).ask(text, sunny.history)
            new = [{"kind": "sunny", "text": reply.text, "error": reply.error}]
            sunny.entries.append({"kind": "user", "text": text})
            remembered = reply.text
            if reply.actions:
                proposal = Proposal(uuid.uuid4().hex[:12], reply.actions)
                sunny.proposals[proposal.id] = proposal
                new.append({"kind": "card", "proposal": proposal})
                remembered += "\n[Proposed, waiting for Progen's Confirm: " + "; ".join(a.label for a in reply.actions) + "]"
            sunny.entries.extend(new)
            if not reply.error:
                sunny.history += [{"role": "user", "content": text}, {"role": "assistant", "content": remembered}]
        return render(request, "_sunny_entries.html", entries=new)

    @router.post("/confirm/{proposal_id}")
    def confirm(request: Request, proposal_id: str):
        """Voert de voorgestelde acties uit via de gewone command handlers."""
        with sunny._lock:
            proposal = sunny.proposals.get(proposal_id)
            if proposal is None or proposal.state != "pending":
                return render(request, "_sunny_result.html", proposal=proposal, note=None, toast=None)
            done, failed = [], []
            for action in proposal.actions:
                try:
                    sunny.data.handler(action.domain).handle(action.command)
                    done.append(action)
                except DomainError as exc:
                    failed.append(f"{action.label}: {exc}")
            labels = "; ".join(a.label for a in done)
            if failed:
                proposal.state, proposal.problem = "failed", " · ".join(failed)
                note = f"Not everything went through: {proposal.problem}" + (f". Saved: {labels}." if done else ".")
                toast = {"message": "Sunny couldn't save everything", "error": True}
            else:
                proposal.state = "confirmed"
                note = "Done."
                toast = {"message": f"Saved · {labels}", "error": False}
            sunny.entries.append({"kind": "sunny", "text": note, "error": bool(failed)})
            sunny.history.append({"role": "user", "content": f"[Progen clicked Confirm. Saved: {labels or 'nothing'}"
                                                             + (f"; failed: {proposal.problem}" if failed else "") + "]"})
        response = render(request, "_sunny_result.html", proposal=proposal, note=note, toast=toast)
        events = sorted({a.changed for a in done})
        if events:
            response.headers["HX-Trigger"] = ", ".join(events)
        return response

    @router.post("/cancel/{proposal_id}")
    def cancel(request: Request, proposal_id: str):
        with sunny._lock:
            proposal = sunny.proposals.get(proposal_id)
            if proposal is None or proposal.state != "pending":
                return render(request, "_sunny_result.html", proposal=proposal, note=None, toast=None)
            proposal.state = "cancelled"
            note = "Cancelled. What should be different?"
            sunny.entries.append({"kind": "sunny", "text": note, "error": False})
            sunny.history.append({"role": "user", "content": "[Progen clicked Cancel: "
                                                             + "; ".join(a.label for a in proposal.actions) + "]"})
        return render(request, "_sunny_result.html", proposal=proposal, note=note, toast=None)

    @router.post("/reset")
    def reset(request: Request):
        sunny.reset()
        return body(request)

    return router
