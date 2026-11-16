"""The visual page ``docs/ev-charging.html`` opened in a real browser.

The page carries a JavaScript twin of the station. ``site/test/twin.test.mjs`` proves the twin builds
the same messages as the Python original; these tests prove the page built around it works.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.pages, pytest.mark.once]

PAGE = Path(__file__).resolve().parent.parent / "docs" / "ev-charging.html"
VIEWS = ("tour", "live", "wire", "replay", "bugs")
# planted bug -> the live check that must turn red, and no other
BUG_TO_CHECK = {
    "M10": "auth",
    "M11": "owner",
    "M13": "energy",
    "M17": "queue",
    "M21": "unplug",
    "M22": "suspend",
    "M25": "lock",
    "M33": "floor",
}
RUN_AHEAD = (
    "() => { const d = App.views.live.debug;"
    " for (let i = 0; i < 400; i++) d.tick(); for (let i = 0; i < 3; i++) d.probe(); }"
)


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    missing = "Playwright is not installed: pip install -e '.[pages]'"
    sync_api = pytest.importorskip("playwright.sync_api", reason=missing)
    if not PAGE.exists():
        pytest.skip("docs/ev-charging.html is not built: python scripts/build_pages.py")
    with sync_api.sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        yield chromium
        chromium.close()


@pytest.fixture
def page(browser: Any) -> Iterator[Any]:
    context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
    tab = context.new_page()
    tab.errors = []
    tab.on("pageerror", lambda error: tab.errors.append(str(error)))
    yield tab
    context.close()


def open_view(page: Any, view: str) -> None:
    page.goto(f"{PAGE.as_uri()}#{view}")
    page.wait_for_selector(f"#view-{view}.on")


def red_checks(page: Any) -> list[str]:
    result: list[str] = page.eval_on_selector_all(".check.fail", "els => els.map(e => e.dataset.check)")
    return result


@pytest.mark.parametrize("view", VIEWS)
def test_every_view_opens_without_a_script_error(page: Any, view: str) -> None:
    """Each of the five views of the visual page opens by its own address and runs without a script error."""
    open_view(page, view)
    page.wait_for_timeout(400)
    assert page.errors == []
    assert page.locator(f"#view-{view} h2").inner_text().strip()
    assert page.locator(".view.on").count() == 1


@pytest.mark.parametrize(("bug", "check"), BUG_TO_CHECK.items())
def test_planted_bug_turns_its_own_check_red(page: Any, bug: str, check: str) -> None:
    """On the live page, switching on a planted bug turns exactly the matching check red."""
    open_view(page, "live")
    page.click(f"label[for=bug-{bug}]")
    page.evaluate(RUN_AHEAD)
    page.wait_for_timeout(400)
    assert red_checks(page) == [check]
    assert page.errors == []


def test_a_healthy_station_keeps_every_check_green(page: Any) -> None:
    """Without a planted bug, a session with an outage, a limit and a card refusal turns no check red."""
    open_view(page, "live")
    page.click("#live-plug")
    page.click("button.chip:has-text('CARD-BLOCKED')")
    page.click("button.chip:has-text('CARD-OK')")
    page.click("#live-ready")
    page.evaluate("() => { const d = App.views.live.debug; for (let i = 0; i < 200; i++) d.tick(); d.probe(); }")
    page.click("#live-profile")
    page.evaluate("() => { const d = App.views.live.debug; for (let i = 0; i < 200; i++) d.tick(); d.probe(); }")
    page.click("#live-offline")
    page.evaluate("() => { const d = App.views.live.debug; for (let i = 0; i < 300; i++) d.tick(); }")
    page.click("#live-offline")
    page.evaluate("() => { const d = App.views.live.debug; for (let i = 0; i < 200; i++) d.tick(); d.probe(); }")
    page.click("#live-unplug")
    page.wait_for_timeout(400)
    assert red_checks(page) == []
    passed = page.eval_on_selector_all(".check.pass", "els => els.map(e => e.dataset.check)")
    assert {"auth", "queue", "unplug", "floor"} <= set(passed)
    assert page.errors == []


def test_the_live_station_can_speak_both_versions(page: Any) -> None:
    """Switching the live station to OCPP 2.0.1 makes it send that version's messages."""
    open_view(page, "live")
    page.click("#live-plug")
    page.wait_for_timeout(200)
    assert "StatusNotification" in page.locator("#live-wire").inner_text()
    page.click("button.chip:has-text('OCPP 2.0.1')")
    page.click("#live-plug")
    page.click("button.chip:has-text('CARD-OK')")
    page.click("#live-ready")
    page.wait_for_timeout(300)
    text = page.locator("#live-wire").inner_text()
    assert "TransactionEvent" in text
    assert "StartTransaction" not in text


def test_the_message_view_explains_every_field(page: Any) -> None:
    """A message on the wire view is shown whole and broken into fields with a plain explanation."""
    open_view(page, "wire")
    page.click("button.btn:has-text('The session begins')")
    page.wait_for_timeout(200)
    assert page.locator("#wire-msg .wire").count() >= 1
    parts = page.locator("#wire-parts .part")
    assert parts.count() >= 5
    text = page.locator("#wire-parts").inner_text()
    assert "the number that ties the answer to this question" in text
    assert "which socket" in text or "the number on the card" in text


def test_the_two_versions_are_shown_side_by_side(page: Any) -> None:
    """The wire view shows the same moment of a session in both versions of OCPP."""
    open_view(page, "wire")
    page.click("button.btn:has-text('The session begins')")
    page.wait_for_timeout(200)
    cards = page.locator("#wire-compare .card")
    assert cards.count() == 2
    both = cards.nth(0).inner_text() + cards.nth(1).inner_text()
    assert "StartTransaction" in both
    assert "TransactionEvent" in both


def test_language_switch_translates_the_page(page: Any) -> None:
    """The RU button turns the headings and the test titles into Russian, and EN turns them back."""
    open_view(page, "replay")
    english = page.locator("#view-replay h2").inner_text()
    page.click("#lang-ru")
    russian = page.locator("#view-replay h2").inner_text()
    assert english != russian
    assert any("а" <= letter <= "я" for letter in russian.lower())
    assert "связ" in page.locator("details.suite[open]").first.inner_text().lower()
    page.click("#lang-en")
    assert page.locator("#view-replay h2").inner_text() == english


def test_replay_shows_the_messages_of_a_test(page: Any) -> None:
    """Opening a test on the replay view shows its steps and the messages it exchanged, each explained."""
    open_view(page, "replay")
    page.locator("details.suite[open] .test button").first.click()
    detail = page.locator("details.suite[open] .test .detail").first
    assert detail.locator(".fr").count() >= 4
    assert '"BootNotification"' in detail.inner_text() or '"StatusNotification"' in detail.inner_text()


def test_the_bug_hunt_lists_every_planted_bug(page: Any) -> None:
    """The bug hunt shows each planted bug, the line that was changed, and the tests that caught it."""
    open_view(page, "bugs")
    cards = page.locator("#bugs-list .mut")
    assert cards.count() >= 40
    first = cards.first.inner_text()
    assert "caught" in first.lower()
    assert page.locator("#bugs-list .diff").count() >= 40


def test_page_fits_a_phone_screen(browser: Any) -> None:
    """At a phone width of 390 px no view scrolls sideways."""
    context = browser.new_context(viewport={"width": 390, "height": 800})
    tab = context.new_page()
    for view in VIEWS:
        open_view(tab, view)
        tab.wait_for_timeout(300)
        assert tab.evaluate("document.documentElement.scrollWidth") <= 390, view
    context.close()
