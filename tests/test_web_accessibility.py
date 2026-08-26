"""Focused markup contracts for the command-center UI redesign."""

from internshelper import companygroups


def test_shell_exposes_current_navigation_skip_link_and_dialog(client):
    response = client.get("/board")
    assert response.status_code == 200
    assert 'class="skip-link" href="#page"' in response.text
    assert 'href="/board" aria-current="page"' in response.text
    assert 'class="drawer" id="posting-drawer" role="dialog" aria-modal="true"' in response.text
    assert 'aria-labelledby="drawer-title"' in response.text
    assert "Health status:" in response.text


def test_board_uses_semantic_disclosures_and_minimum_window_modes(client):
    response = client.get("/board")
    assert 'class="tier-head" type="button" :aria-expanded="open"' in response.text
    assert 'class="co-toggle" type="button" :aria-expanded="open"' in response.text
    assert 'data-board-panel="inbox"' in response.text
    assert 'data-board-panel="pipeline"' in response.text
    assert 'data-drawer-trigger role="button" tabindex="0"' in response.text
    assert 'aria-label="Stripe Board group"' in response.text


def test_drawer_has_named_controls_and_reading_first_hierarchy(client):
    response = client.get("/board/card/greenhouse:1")
    assert response.status_code == 200
    assert 'id="drawer-title"' in response.text
    assert 'aria-label="Close posting details"' in response.text
    assert "Open original posting" in response.text
    assert response.text.index("Role overview") < response.text.index("Application tracking")
    assert 'role="status"' in response.text
    assert "x-data='{ status: \"Untracked\" }'" in response.text


def test_companies_names_the_two_independent_approval_systems(
    client, company_groups_file
):
    companygroups.propose_discovery(
        company_groups_file,
        "Cloudflare",
        reason="Developer platform",
        evidence="Public product and careers evidence",
    )
    response = client.get("/companies")
    assert response.status_code == 200
    assert "Board grouping &amp; discovery" in response.text
    assert "Careers-board resolution" in response.text
    assert "Approve Board group" in response.text
    assert "never configures a source" in response.text
    assert "Source approval cannot promote or demote a company" in response.text


def test_postings_filters_and_external_actions_are_named(client):
    response = client.get("/postings")
    assert response.status_code == 200
    assert '<span class="field-label">Search</span>' in response.text
    assert '<span class="field-label">Posting state</span>' in response.text
    assert '<span class="field-label">Source</span>' in response.text
    assert 'aria-label="Open Software Engineer Intern at Stripe"' in response.text


def test_sources_render_as_managed_connections(client, sources_file):
    sources_file.write_text("sources:\n  - type: greenhouse\n    token: stripe\n")
    response = client.get("/sources")
    assert response.status_code == 200
    assert "Registered sources" in response.text
    assert "Manage" in response.text
    assert "Historical postings remain" in response.text


def test_styles_and_script_include_responsive_focus_contracts(client):
    css = client.get("/static/css/app.css").text
    script = client.get("/static/js/app.js").text
    assert '@media (max-width: 1100px)' in css
    assert 'html[data-board-panel="inbox"] .board-pipeline' in css
    assert "prefers-reduced-motion" in css
    assert "shell.inert = true" in script
    assert "drawerOpener.focus()" in script
    assert "reduced ? 0 : 140" in script
