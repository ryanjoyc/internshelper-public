"""Optional real-browser contracts for the responsive Board shell.

Run with ``.venv/bin/python -m pytest -m browser`` after installing the ``ui``
extra and Playwright's Chromium build. The normal suite excludes these checks.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import pytest

from internshelper import db
from internshelper.availability import SourceAuthority, UserState
from internshelper.availability_checks import (
    CheckStage,
    InvestigationFinding,
    InvestigationObservation,
    NetworkObservation,
    evaluate_availability,
)
from internshelper.availability_store import persist_evaluation

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import Page, expect


pytestmark = pytest.mark.browser


@pytest.fixture
def live_server(seeded_db, sources_file, companies_file, company_groups_file):
    """Serve the fixture-backed app on an ephemeral loopback port."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "internshelper.web",
            "--port",
            str(port),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=os.environ.copy(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(80):
            if process.poll() is not None:
                raise RuntimeError("browser-test web server exited during startup")
            try:
                with urlopen(f"{base_url}/healthz", timeout=0.2) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("browser-test web server did not start")
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _document_fits(page: Page) -> bool:
    return page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )


def test_page_matrix_has_no_horizontal_document_overflow(page: Page, live_server: str):
    for width in (980, 1143, 1144, 1440):
        page.set_viewport_size({"width": width, "height": 720})
        for path in ("/board", "/postings", "/companies", "/sources", "/health"):
            page.goto(f"{live_server}{path}")
            expect(page.locator("main#page")).to_be_visible()
            assert _document_fits(page), f"{path} overflows at {width}px"


def test_board_breakpoint_keeps_header_controls_aligned(page: Page, live_server: str):
    for width in (1101, 1120, 1143, 1144):
        page.set_viewport_size({"width": width, "height": 700})
        page.goto(f"{live_server}/board")
        mode = page.locator(".board-mode")
        expect(mode).to_be_visible() if width <= 1143 else expect(mode).to_be_hidden()

        board_table = page.locator(".page-head-actions > .seg")
        exceptions = page.locator(".page-head-actions > .view-menu")
        board_box = board_table.bounding_box()
        exceptions_box = exceptions.bounding_box()
        assert board_box and exceptions_box
        assert board_box["height"] == exceptions_box["height"] == 36
        assert board_box["y"] == exceptions_box["y"]
        assert _document_fits(page)


def test_popover_and_drawer_keyboard_focus_contract(page: Page, live_server: str):
    page.set_viewport_size({"width": 980, "height": 640})
    page.goto(f"{live_server}/board")
    page.evaluate("localStorage.clear()")
    page.reload()

    menu = page.locator("details[data-popover]").first
    menu.locator(":scope > summary").click()
    expect(menu).to_have_attribute("open", "")
    page.keyboard.press("Escape")
    expect(menu).not_to_have_attribute("open", "")
    expect(menu.locator(":scope > summary")).to_be_focused()

    menu.locator(":scope > summary").click()
    page.locator("main#page").click(position={"x": 2, "y": 2})
    expect(menu).not_to_have_attribute("open", "")

    page.locator(".co-toggle:visible").first.click()
    opener = page.locator(".card-open:visible").first
    opener.click()
    expect(page.locator("#posting-drawer")).to_be_visible()
    expect(page.locator("#posting-drawer .btn-icon").first).to_be_focused()
    assert page.locator(".shell").evaluate("element => element.inert") is True

    flag_trigger = page.get_by_role("button", name="Flag for investigation")
    flag_trigger.click()
    expect(page.get_by_label("Reason for flagging")).to_be_focused()
    page.locator("form.drawer-inline-form").get_by_role("button", name="Cancel").click()
    expect(flag_trigger).to_be_focused()

    dismiss_trigger = page.get_by_role("button", name="Dismiss posting")
    dismiss_trigger.click()
    expect(page.get_by_role("button", name="Confirm dismissal")).to_be_focused()
    page.locator(".confirm-inline").get_by_role("button", name="Cancel").click()
    expect(dismiss_trigger).to_be_focused()

    # Saving replaces both the drawer body and the Board out-of-band. Focus must
    # return to the new opener node, not the detached node that initiated the drawer.
    page.locator("#posting-drawer textarea[name='notes']").fill("browser focus check")
    page.get_by_role("button", name="Save changes").click()
    expect(page.get_by_text("All changes saved locally")).to_be_visible()

    page.keyboard.press("Escape")
    expect(page.locator("#posting-drawer")).to_be_hidden()
    expect(opener).to_be_focused()
    assert page.locator(".shell").evaluate("element => element.inert") is False

    opener.click()
    page.get_by_role("button", name="Dismiss posting").click()
    page.get_by_role("button", name="Confirm dismissal").click()
    expect(page.locator("#posting-drawer")).to_be_hidden()
    expect(page.locator(".card-open:visible").first).to_be_focused()


def test_availability_table_confirmation_opens_drawer_and_preserves_focus(
    page: Page, live_server: str, seeded_db: Path
):
    """The table's primary availability action survives its HTMX drawer swap."""
    now = "2026-08-30T12:00:00+00:00"
    conn = db.connect(seeded_db)
    candidate = "https://jobs.example.test/replacement"
    evaluation = evaluate_availability(
        source_authority=SourceAuthority.FIRST_PARTY_ATS,
        user_state=UserState.ORDINARY,
        observations=(
            NetworkObservation(
                sequence=1,
                observed_at=now,
                stage=CheckStage.INITIAL_VALIDATION,
                target="original_url",
                attempt=1,
                status=404,
                body="Page not found",
                employer_hosted=True,
            ),
            InvestigationObservation(
                sequence=2,
                observed_at=now,
                stage=CheckStage.USER_INVESTIGATION,
                target="replacement_candidate",
                attempt=1,
                finding=InvestigationFinding.REPLACEMENT_FOUND,
                candidate_url=candidate,
            ),
        ),
        investigation_stages=(
            "queued",
            "review_finding",
            "await_user_confirmation",
        ),
    )
    persist_evaluation(conn, "greenhouse:1", evaluation, updated_at=now)
    conn.close()

    page.goto(f"{live_server}/board?view=table")
    confirm = page.get_by_role("button", name="Confirm replacement")
    confirm.focus()
    expect(confirm).to_be_focused()
    with page.expect_response(
        lambda response: "/board/card/greenhouse:1/replacement/confirm" in response.url
    ):
        confirm.click()

    result = page.locator("#availability-result-greenhouse-1")
    expect(page.locator("#posting-drawer")).to_be_visible()
    expect(result).to_be_visible()
    expect(result).to_be_focused()
    expect(result).to_contain_text("Replacement confirmed")
    expect(result).to_contain_text(candidate)
    expect(page.get_by_role("button", name="Use original link again")).to_be_visible()


def test_board_keyboard_shortcuts_continue_from_the_selected_card(
    page: Page, live_server: str
):
    page.set_viewport_size({"width": 1440, "height": 720})
    page.goto(f"{live_server}/board")
    page.evaluate("localStorage.clear()")
    page.reload()
    page.locator(".co-toggle").first.click()
    page.locator("main#page").focus()

    page.keyboard.press("j")
    selected = page.locator(".board-col--inbox .board-card.is-selected")
    expect(selected).to_have_count(1)
    first_id = selected.get_attribute("data-id")

    page.keyboard.press("j")
    expect(selected).to_have_count(1)
    assert selected.get_attribute("data-id") != first_id
    page.keyboard.press("k")
    assert selected.get_attribute("data-id") == first_id
    page.keyboard.press("Escape")
    expect(selected).to_have_count(0)


def test_quick_status_preserves_focus_notes_and_error_feedback(
    page: Page, live_server: str
):
    page.set_viewport_size({"width": 1440, "height": 720})
    response = page.request.post(
        f"{live_server}/board/move",
        form={"posting_id": "greenhouse:0", "status": "Applied"},
    )
    assert response.ok
    page.goto(f"{live_server}/board")

    applied_trigger = page.get_by_label(
        "Change application status. Current status: Applied"
    )
    applied_trigger.focus()
    page.keyboard.press("ArrowDown")
    expect(applied_trigger).to_be_focused()

    applied_trigger.click()
    page.get_by_label("Offer", exact=True).check()
    page.locator("[data-status-notes] textarea").fill("Saved from quick status")
    page.get_by_role("button", name="Move to Offer").click()
    offer_trigger = page.get_by_label(
        "Change application status. Current status: Offer"
    )
    expect(offer_trigger).to_be_focused()
    page.wait_for_function(
        "!document.querySelector('#board-region').classList.contains('htmx-settling')"
    )

    page.locator(
        ".board-pipeline .board-card[data-id='greenhouse:0'] .card-open"
    ).click()
    expect(page.locator("#posting-drawer")).to_be_visible()
    expect(page.locator("#posting-drawer textarea[name='notes']")).to_have_value(
        "Saved from quick status"
    )
    page.keyboard.press("Escape")

    page.locator("#toast-region .toast").get_by_role("button", name="Undo").click()
    restored_trigger = page.get_by_label(
        "Change application status. Current status: Applied"
    )
    expect(restored_trigger).to_be_visible()
    page.locator(
        ".board-pipeline .board-card[data-id='greenhouse:0'] .card-open"
    ).click()
    expect(page.locator("#posting-drawer textarea[name='notes']")).to_have_value("")
    page.keyboard.press("Escape")

    page.evaluate(
        """htmx.ajax('POST', '/board/move', {
          values: {posting_id: 'greenhouse:0', status: 'Ghosted'},
          target: '#board-region', swap: 'innerHTML'
        })"""
    )
    expect(page.get_by_role("alert")).to_contain_text("status must be one of")


def test_quick_status_focus_falls_back_to_a_hidden_target_lane(
    page: Page, live_server: str
):
    page.set_viewport_size({"width": 1440, "height": 720})
    response = page.request.post(
        f"{live_server}/board/move",
        form={"posting_id": "greenhouse:0", "status": "Applied"},
    )
    assert response.ok
    page.goto(f"{live_server}/board")
    page.get_by_role("button", name="Hide Offer lane").click()
    show_offer = page.get_by_role("button", name="Show Offer lane")
    expect(show_offer).to_be_visible()
    expect(show_offer).to_be_focused()

    applied_trigger = page.get_by_label(
        "Change application status. Current status: Applied"
    )
    applied_trigger.click()
    page.get_by_label("Offer", exact=True).check()
    page.get_by_role("button", name="Move to Offer").click()
    expect(show_offer).to_be_focused()

    show_offer.click()
    expect(page.get_by_role("button", name="Hide Offer lane")).to_be_focused()


def test_board_group_change_restores_focus_to_replacement_control(
    page: Page, live_server: str
):
    page.set_viewport_size({"width": 1440, "height": 720})
    page.goto(f"{live_server}/board")
    group = page.get_by_label("Stripe Board group")
    with page.expect_response(lambda response: "/board/company-group" in response.url):
        group.select_option("known")
    expect(page.get_by_label("Stripe Board group")).to_be_focused()

    page.goto(f"{live_server}/companies")
    company_group = page.get_by_label("Stripe Board group")
    with page.expect_response(lambda response: "/companies/group/set" in response.url):
        company_group.select_option("top_target")
    expect(page.get_by_label("Stripe Board group")).to_be_focused()


def test_source_remove_confirmation_moves_and_restores_focus(
    page: Page, live_server: str, sources_file: Path
):
    sources_file.write_text(
        "sources:\n  - type: greenhouse\n    token: stripe\n",
        encoding="utf-8",
    )
    page.goto(f"{live_server}/sources")
    page.locator("details.source-manage > summary").click()
    remove_trigger = page.get_by_role("button", name="Remove source")
    remove_trigger.click()
    expect(page.get_by_role("button", name="Confirm removal")).to_be_focused()
    page.get_by_role("button", name="Cancel").click()
    expect(remove_trigger).to_be_focused()

    remove_trigger.click()
    page.get_by_role("button", name="Confirm removal").click()
    expect(page.locator("#source-list")).to_be_focused()
    expect(page.get_by_text("No sources configured")).to_be_visible()
