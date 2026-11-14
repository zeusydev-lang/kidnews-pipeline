"""Local admin page against the Neon test branch."""

import re

import pytest
from fastapi.testclient import TestClient

from admin.app import app
from core import stories

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_db")]


@pytest.fixture
def client():
    return TestClient(app, base_url="http://127.0.0.1")


def form_token(client: TestClient, story_id: int) -> str:
    page = client.get(f"/stories/{story_id}").text
    return re.search(r'name="form_token" value="([^"]+)"', page).group(1)


def test_lists_drafts(client, make_draft):
    stories.save_draft(make_draft(1))
    page = client.get("/stories?status=draft")
    assert page.status_code == 200
    assert "Kid title 1" in page.text
    assert "Draft (1)" in page.text


def test_story_page_shows_quiz_and_source(client, make_draft):
    story_id = stories.save_draft(make_draft(1))
    page = client.get(f"/stories/{story_id}").text
    assert "Quiz (4 questions)" in page
    assert "PRIVATE SOURCE TEXT" in page  # admin may see the original text
    assert "Approve" in page


def test_missing_story_is_404(client):
    assert client.get("/stories/999999").status_code == 404


def test_approve(client, make_draft):
    story_id = stories.save_draft(make_draft(1))
    r = client.post(
        f"/stories/{story_id}/approve",
        data={"form_token": form_token(client, story_id)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert stories.get_story(story_id)["status"] == "approved"


def test_reject_with_reason(client, make_draft):
    story_id = stories.save_draft(make_draft(1))
    client.post(
        f"/stories/{story_id}/reject",
        data={
            "form_token": form_token(client, story_id),
            "reason": "Quiz Q3 not in story",
        },
    )
    story = stories.get_story(story_id)
    assert story["status"] == "rejected"
    assert story["review_notes"] == "Quiz Q3 not in story"


def test_reject_without_reason_shows_error(client, make_draft):
    story_id = stories.save_draft(make_draft(1))
    r = client.post(
        f"/stories/{story_id}/reject",
        data={"form_token": form_token(client, story_id), "reason": "  "},
    )
    assert "Please write a reason" in r.text
    assert stories.get_story(story_id)["status"] == "draft"


def test_approve_twice_shows_error(client, make_draft):
    story_id = stories.save_draft(make_draft(1))
    token = form_token(client, story_id)
    client.post(f"/stories/{story_id}/approve", data={"form_token": token})
    r = client.post(f"/stories/{story_id}/approve", data={"form_token": token})
    assert "it is approved" in r.text


@pytest.mark.parametrize("token", ["", "wrong-token"])
def test_form_without_valid_token_is_refused(client, make_draft, token):
    """Another website can't approve stories through your browser."""
    story_id = stories.save_draft(make_draft(1))
    r = client.post(f"/stories/{story_id}/approve", data={"form_token": token})
    assert r.status_code == 403
    assert stories.get_story(story_id)["status"] == "draft"


def test_other_host_names_are_refused(make_draft):
    """Blocks DNS-rebinding: a page on evil.example can't talk to the admin."""
    evil = TestClient(app, base_url="http://evil.example")
    assert evil.get("/stories").status_code == 400


def test_story_text_is_escaped(client, make_draft):
    story_id = stories.save_draft(make_draft(1, kid_title="<script>alert(1)</script>"))
    page = client.get(f"/stories/{story_id}").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_attribution_warning(client, make_draft):
    story_id = stories.save_draft(
        make_draft(1, summary="According to NASA, ice is melting.")
    )
    assert "Attribution warning" in client.get(f"/stories/{story_id}").text


def test_story_page_shows_check_results(client, make_draft):
    from core.models import EvalResult

    evals = [
        EvalResult(check_name="reading_level", passed=True, details={"grade": 6.8}),
        EvalResult(
            check_name="grounding",
            passed=False,
            details={
                "claims_checked": 9,
                "unsupported": [
                    {"claim": "El Nino caused the storms.", "evidence": "Only linked."}
                ],
            },
            judge_model="anthropic/claude-sonnet-5-5",
        ),
    ]
    story_id = stories.save_draft(make_draft(1), evals)
    page = client.get(f"/stories/{story_id}").text
    assert "Checks (1/2 passed)" in page
    assert "El Nino caused the storms." in page
    assert "grade 6.8" in page


def test_story_page_without_check_results(client, make_draft):
    story_id = stories.save_draft(make_draft(1))
    assert "No check results stored" in client.get(f"/stories/{story_id}").text
