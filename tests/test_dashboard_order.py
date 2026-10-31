"""The Reading and Watching dashboards keep the order of their cards when they refresh after your own
action; navigating starts again with the latest update first."""
import re

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.web import keep_order


class Item:
    def __init__(self, item_id):
        self.id = item_id


def ids(entries):
    return [e.id for e in entries]


# ---- keep_order ----

def test_without_an_order_the_normal_order_stays():
    entries = [Item("c"), Item("b"), Item("a")]
    assert keep_order(entries, "", lambda e: e.id) is entries


def test_known_entries_keep_their_place_new_ones_go_in_front_and_gone_ones_drop_out():
    now = [Item("a"), Item("d"), Item("b"), Item("e")]  # a was just updated, d and e are new, c is gone
    assert ids(keep_order(now, "c,b,a", lambda e: e.id)) == ["d", "e", "b", "a"]


def test_an_entry_that_was_not_on_screen_counts_as_new():
    assert ids(keep_order([Item("a"), Item("b")], "b", lambda e: e.id)) == ["a", "b"]


# ---- The dashboards ----

@pytest.fixture
def client():
    return TestClient(create_app(":memory:"), headers={"HX-Request": "true"})


def dashboard(client, tracker, order=None):
    """(titles of the cards in order, the order the page sends along when it refreshes itself)"""
    page = client.get(f"/ui/{tracker}/dashboard", params={"order": order} if order is not None else {}).text
    titles = re.findall(r'class="series-title" title="([^"]+)"', page)
    refresh = re.search(rf'hx-get="/ui/{tracker}/dashboard(?:\?order=([^"]*))?"', page)
    return titles, (refresh.group(1) or "").split(",") if refresh.group(1) else []


READING = {
    "tracker": "reading",
    "add": lambda c, title: c.post("/ui/reading/series", data={"title": title, "kind": "manhwa", "source": "x",
                                                                "start_chapter": "1"}),
    "bump": lambda c, sid: c.post(f"/ui/reading/series/{sid}/progress", data={"chapter": "2"}),
    "hold": lambda c, sid: c.post(f"/ui/reading/series/{sid}/status", data={"status": "on_hold"}),
}
WATCHING = {
    "tracker": "watching",
    "add": lambda c, title: c.post("/ui/watching/shows", data={"title": title, "kind": "series"}),
    "bump": lambda c, sid: c.post(f"/ui/watching/shows/{sid}/status", data={"status": "rewatching"}),
    "hold": lambda c, sid: c.post(f"/ui/watching/shows/{sid}/status", data={"status": "on_hold"}),
}


@pytest.fixture(params=[READING, WATCHING], ids=["reading", "watching"])
def domain(request):
    return request.param


def set_up(client, domain, *titles):
    for title in titles:
        assert domain["add"](client, title).status_code == 200
    return dashboard(client, domain["tracker"])


def test_the_normal_order_is_the_latest_update_first(client, domain):
    titles, order = set_up(client, domain, "A", "B", "C")
    assert titles == ["C", "B", "A"] and len(order) == 3


def test_a_refresh_with_the_order_keeps_the_card_you_updated_in_place(client, domain):
    _, order = set_up(client, domain, "A", "B", "C")
    domain["bump"](client, order[2])  # A, the last card, is now the most recently updated
    assert dashboard(client, domain["tracker"])[0] == ["A", "C", "B"]                          # navigating: A on top
    assert dashboard(client, domain["tracker"], ",".join(order))[0] == ["C", "B", "A"]         # refreshing: same places


def test_a_new_series_comes_in_front(client, domain):
    _, order = set_up(client, domain, "A", "B", "C")
    domain["bump"](client, order[2])
    domain["add"](client, "D")
    assert dashboard(client, domain["tracker"], ",".join(order))[0] == ["D", "C", "B", "A"]


def test_a_series_that_leaves_the_list_just_disappears(client, domain):
    _, order = set_up(client, domain, "A", "B", "C")
    domain["hold"](client, order[0])  # C goes on hold
    assert dashboard(client, domain["tracker"], ",".join(order))[0] == ["B", "A"]


def test_the_page_passes_on_the_order_for_its_own_refresh(client, domain):
    _, order = set_up(client, domain, "A", "B", "C")
    domain["bump"](client, order[2])
    _, passed_on = dashboard(client, domain["tracker"], ",".join(order))
    assert passed_on == order  # the next refresh keeps the same places again


def test_navigating_sends_no_order_and_sorts_on_the_latest_update(client, domain):
    _, order = set_up(client, domain, "A", "B", "C")
    domain["bump"](client, order[1])  # B
    titles, _ = dashboard(client, domain["tracker"])
    assert titles == ["B", "C", "A"]


def test_only_six_cards_show_but_the_hidden_ones_keep_their_place_too(client, domain):
    titles, order = set_up(client, domain, *"ABCDEFGH")
    assert titles == list("HGFEDC") and len(order) == 8      # all eight are in the order, six are shown
    domain["bump"](client, order[5])                           # C, the last one on screen
    assert dashboard(client, domain["tracker"], ",".join(order))[0] == list("HGFEDC")  # nothing moved, nothing jumped in
    assert dashboard(client, domain["tracker"])[0] == list("CHGFED")


def test_a_hidden_series_that_is_new_to_the_page_may_appear_in_front(client, domain):
    titles, order = set_up(client, domain, *"ABCDEFGH")
    domain["add"](client, "I")
    assert dashboard(client, domain["tracker"], ",".join(order))[0] == list("IHGFED")
