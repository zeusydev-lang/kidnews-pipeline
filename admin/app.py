"""Local admin page: review drafts, approve or reject them.

Runs ONLY on your laptop (127.0.0.1) with the kidnews_admin login, which can
change status and review notes and nothing else.

    uv run python -m admin                                  # dev
    uv run --env-file .env.production python -m admin       # production

Protections, even though it's local:
  - Only answers requests addressed to 127.0.0.1 / localhost (blocks DNS-rebinding).
  - Every form carries a secret token, so other websites can't submit
    approve/reject from your browser (blocks CSRF).
  - Templates escape all story text, so AI output can't inject HTML.
"""

import re
import secrets
from collections import Counter
from pathlib import Path
from urllib.parse import quote, urlsplit

from dotenv import dotenv_values
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from core import stories
from core.checks import attribution_problems
from core.settings import database_url

ROOT = Path(__file__).resolve().parent.parent
STATUSES = ("draft", "approved", "rejected", "published")
FORM_TOKEN = secrets.token_urlsafe(32)  # new every time the server starts

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

app = FastAPI(title="KidNews admin", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


def branch_name() -> str:
    """Which Neon branch the admin login points at: dev, test, production or unknown."""
    host = urlsplit(database_url("admin")).hostname
    for file, label in (
        (".env.production", "production"),
        (".env.test", "test"),
        (".env", "dev"),
    ):
        path = ROOT / file
        url = dotenv_values(path).get("DATABASE_URL") if path.exists() else None
        if url and urlsplit(url).hostname == host:
            return label
    return "unknown"


BRANCH = branch_name()


def attribution_warnings(story: dict) -> list[str]:
    """Phrases that wrongly attribute AI-written text to NASA."""
    fields = [story.get(k) or "" for k in ("kid_title", "summary", "body")]
    for q in story.get("questions") or []:
        fields += [q.get("prompt", ""), q.get("explanation", ""), *q.get("options", [])]
    return attribution_problems(*fields)


def render(request: Request, name: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(
        request, name, {"branch": BRANCH, "form_token": FORM_TOKEN, **context}
    )


def check_token(token: str) -> None:
    if not secrets.compare_digest(token or "", FORM_TOKEN):
        raise HTTPException(
            403, "Form token is missing or old. Reload the page and try again."
        )


def back_to(path: str, **params: str) -> RedirectResponse:
    query = "&".join(f"{k}={quote(v)}" for k, v in params.items() if v)
    return RedirectResponse(f"{path}?{query}" if query else path, status_code=303)


# --- Pages -------------------------------------------------------------------


@app.get("/")
def home():
    return RedirectResponse("/stories?status=draft", status_code=303)


@app.get("/stories", response_class=HTMLResponse)
def story_list(request: Request, status: str = "draft", msg: str = "", error: str = ""):
    if status not in (*STATUSES, "all"):
        status = "draft"
    everything = stories.list_stories(limit=1000)
    counts = Counter(s["status"] for s in everything)
    rows = (
        everything
        if status == "all"
        else [s for s in everything if s["status"] == status]
    )
    return render(
        request,
        "list.html",
        rows=rows,
        status=status,
        statuses=STATUSES,
        counts=counts,
        total=len(everything),
        msg=msg,
        error=error,
    )


@app.get("/stories/{story_id}", response_class=HTMLResponse)
def story_page(request: Request, story_id: int, msg: str = "", error: str = ""):
    try:
        story = stories.get_story(story_id)
    except stories.StoryNotFound:
        raise HTTPException(404, f"No story {story_id}") from None
    paragraphs = [
        p.strip() for p in re.split(r"\n\s*\n", story.get("body") or "") if p.strip()
    ]
    return render(
        request,
        "story.html",
        story=story,
        paragraphs=paragraphs,
        warnings=attribution_warnings(story),
        msg=msg,
        error=error,
    )


# --- Actions -----------------------------------------------------------------


@app.post("/stories/{story_id}/approve")
def approve(story_id: int, form_token: str = Form("")):
    check_token(form_token)
    try:
        stories.approve(story_id)
    except stories.StoryNotFound:
        raise HTTPException(404, f"No story {story_id}") from None
    except stories.InvalidStatusChange as err:
        return back_to(f"/stories/{story_id}", error=str(err))
    return back_to("/stories", status="draft", msg=f"Approved story {story_id}.")


@app.post("/stories/{story_id}/reject")
def reject(story_id: int, form_token: str = Form(""), reason: str = Form("")):
    check_token(form_token)
    try:
        stories.reject(story_id, reason)
    except stories.StoryNotFound:
        raise HTTPException(404, f"No story {story_id}") from None
    except ValueError:
        return back_to(
            f"/stories/{story_id}", error="Please write a reason before rejecting."
        )
    except stories.InvalidStatusChange as err:
        return back_to(f"/stories/{story_id}", error=str(err))
    return back_to("/stories", status="draft", msg=f"Rejected story {story_id}.")
