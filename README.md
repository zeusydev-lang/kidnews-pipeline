# kidnews-pipeline

A daily pipeline that turns one NASA Earth Observatory article into a
middle-school story (summary, concepts, glossary) and a quiz. Stories are
stored in Postgres, approved by a human, and published as a static website.

## Project layout

| Folder        | Purpose                                      |
|---------------|----------------------------------------------|
| `agent/`      | Pipeline steps: ingest, summarize, quiz, checks |
| `core/`       | Business layer: settings, database connections, validation, story functions |
| `publish/`    | Publish script: approved stories to `web/data/` |
| `web/`        | Site templates and output                    |
| `migrations/` | SQL schema files, migration runner, login setup |
| `admin/`      | Local admin page for approving and rejecting drafts |
| `scripts/`    | Helpers, e.g. sample drafts for dev |
| `tests/`      | Validation, business layer and permission tests |

## Setup

1. Install [uv](https://docs.astral.sh/uv/).
2. Install dependencies:
   ```
   uv sync
   ```
3. Copy `.env.example` to `.env` and set `DATABASE_URL` to the `dev` branch
   owner connection string (see below).
4. Create the tables and the limited logins on `dev`:
   ```
   uv run python migrations/migrate.py
   uv run python migrations/logins.py --env-file .env
   ```

## Database (Neon Postgres)

The Neon project has three branches, each with its own env file.
None of these files is committed to git.

| Neon branch  | Use                       | Env file |
|--------------|---------------------------|----------|
| `dev`        | Building and trying things | `.env` |
| `test`       | Automated tests (wiped on every test) | `.env.test` |
| `production` | The live site             | `.env.production` (local), GitHub Secrets (CI) |

Each env file starts with one line, the branch's **owner** connection string.
In Neon: **Connect** → choose the branch, database `neondb`, role
`neondb_owner`, pooling **off** (host without `-pooler`):

```
DATABASE_URL="postgresql://neondb_owner:...@ep-.../neondb?sslmode=require"
```

`logins.py` then adds the three limited logins to the same file.

### Tables

- `publishers`: where articles come from, with license notes. Seeded with NASA Earth Observatory.
- `stories`: one row per source article plus its AI-written story. Unique on `(publisher_id, source_url)` and on `slug`. `source_text` and `review_notes` are private and never published.
- `quizzes`: one quiz per story; 4 to 5 questions stored as a JSON array.

### Story status rules (enforced in the database)

```
draft ──► approved ──► published
  │
  └────► rejected   (review_notes reason required)
```

- New stories always start as `draft`. Any other change is refused.
- `approved` and `published` need `kid_title`, `summary`, `body` and `slug`.
- `approved_at` and `published_at` are set automatically by a trigger.

### Database logins

Each part of the pipeline uses its own login with only the rights it needs.

| Login | Used by | Can | Cannot |
|---|---|---|---|
| `neondb_owner` | Migration runner only | Everything | Never used by the agent, admin page or GitHub |
| `kidnews_agent` | Agent | Read publishers; insert draft stories and quizzes | Set status, update, delete, read `source_text` back |
| `kidnews_admin` | Local admin page | Read everything; change `status` and `review_notes` | Insert, delete, edit story text |
| `kidnews_publish` | Publish script | Read the `public_stories` view; call `mark_published(id)` | Read `source_text` or `review_notes`; update stories directly |

The roles and permissions are created by `003_roles.sql` without passwords.
`logins.py` turns on login with random passwords and writes
`AGENT_DATABASE_URL`, `ADMIN_DATABASE_URL` and `PUBLISH_DATABASE_URL` into the
env file you name. Passwords are never printed. Running it again rotates all three.

### Running migrations

The runner applies every `.sql` file in `migrations/` in name order
(`001_`, `002_`, ...). Each file runs in its own transaction, so a failed
file makes no changes. It prints the target host before applying anything.

| Branch | Commands |
|---|---|
| `dev` | `uv run python migrations/migrate.py`<br>`uv run python migrations/logins.py --env-file .env` |
| `test` | `uv run --env-file .env.test python migrations/migrate.py`<br>`uv run python migrations/logins.py --env-file .env.test` |
| `production` | `uv run --env-file .env.production python migrations/migrate.py`<br>`uv run python migrations/logins.py --env-file .env.production` |

`logins.py` is only needed once per branch (or to rotate passwords).
On `production`, check that the printed `Target:` host matches the
`production` host in Neon.

### Adding a migration

1. Create the next numbered file, for example `migrations/004_add_something.sql`.
2. Make it safe to run again: use `create table if not exists`,
   `add column if not exists`, `drop constraint if exists` before re-adding,
   and `on conflict do nothing`.
3. Run it on `dev`, then `test`, and run the tests.
4. Run it on `production` when going live.

## Business layer (`core/`)

All reads and writes go through `core/stories.py`. Each function uses the
least-privileged login for its job.

| Function | Login | What it does |
|---|---|---|
| `find_existing(publisher_id, source_url)` | agent | Story id if the article is already stored (check before calling Claude) |
| `save_draft(draft)` | agent | Saves story and quiz together as a draft. Raises `DuplicateStory` or `SlugTaken` |
| `list_stories(status=None)` | admin | Stories for the admin page, newest first |
| `get_story(id)` | admin | Full story with publisher name and quiz |
| `approve(id)` | admin | draft → approved |
| `reject(id, reason)` | admin | draft → rejected, reason required |
| `get_publishable()` | publish | Approved stories, public fields only |
| `get_published()` | publish | Live stories, for rebuilding the site index |
| `mark_published(id)` | publish | approved → published |

Invalid changes raise `InvalidStatusChange`; unknown ids raise `StoryNotFound`.
Story and quiz shapes are checked by `core/models.py` (`StoryDraft`, `Quiz`).

## Tests

```
uv run pytest
```

- `tests/test_models.py`: story and quiz validation. No database needed.
- `tests/test_stories.py`: the business layer, end to end.
- `tests/test_permissions.py`: each login is refused anything outside its job,
  and the status rules hold even for the owner.

Database tests run only against the Neon `test` branch (`.env.test`) and wipe
the story tables before each test. If `.env.test` is missing they are skipped;
if it points at the same database as `.env` or `.env.production`, the run stops.

## Website (`web/`)

Plain HTML, CSS and JavaScript. No build step, no frameworks, and nothing
loaded from other sites (no fonts, scripts or trackers).

| File | Purpose |
|---|---|
| `index.html` + `js/index.js` | Story cards, newest first, from `data/index.json` |
| `story.html` + `js/story.js` | One story (`story.html?slug=...`): body, big ideas, words to know, quiz, source credit |
| `about.html` | How stories are made, privacy statement |
| `css/style.css` | Phone-first layout, light and dark mode |
| `_headers` | Cloudflare Pages security headers (Content-Security-Policy, no referrer) |

Story text is AI-written, so the pages treat it as untrusted: text is added
with `textContent` (never `innerHTML`), slugs are checked before loading, and
only `http(s)` or local `images/` URLs are used for links and images.

Preview locally with dev data:

```
uv run python -m publish.build
uv run python -m http.server 8000 --bind 127.0.0.1 -d web
```

Then open http://127.0.0.1:8000.

### Data files (written by the publish script)

`web/data/` and `web/images/stories/` are generated and **never committed**
(they're in `.gitignore`). Locally they hold dev data for previewing; the live
site gets production data built fresh by `publish.deploy`.

`data/index.json`:

```json
{
  "generated_at": "2026-11-11T12:00:00Z",
  "stories": [
    { "slug": "...", "kid_title": "...", "summary": "...", "topic": "...",
      "image_url": "...", "image_credit": "...", "published_at": "..." }
  ]
}
```

`data/stories/<slug>.json` holds the public fields from the `public_stories`
view: `slug`, `kid_title`, `summary`, `body` (paragraphs separated by blank
lines), `concepts` (`[{name, explanation}]`), `glossary` (`[{term, definition}]`),
`topic`, `reading_level`, `grade_band`, `image_url`, `image_credit`,
`source_title`, `source_url`, `source_author`, `source_published_on`,
`publisher_name`, `published_at`, and `questions`
(`[{prompt, options, correct_index, explanation}]`).

## Admin page (`admin/`)

A local web page for reviewing drafts. It runs only on your laptop
(`127.0.0.1`) and uses the `kidnews_admin` login.

```
uv run python -m admin                                 # dev database
uv run --env-file .env.production python -m admin      # production (red banner)
```

Then open http://127.0.0.1:8001. Each draft shows the summary, story, big
ideas, words to know, the quiz with correct answers marked, the source link,
the original text and a review checklist. Approve, or reject with a reason.
A warning appears if the text contains wording like "according to NASA".

Protections: it only answers requests for `127.0.0.1`/`localhost`, every form
has a secret token (so other websites can't approve stories through your
browser), and all story text is escaped.

## Publish script (`publish/`)

```
uv run python -m publish.build
```

1. Marks every approved story as published (`kidnews_publish` login).
2. Reads all published stories from the `public_stories` view.
3. Downloads each story image into `web/images/stories/` so visitors never
   load images from other sites. If a download fails, the story has no image.
4. Rebuilds `web/data/` from scratch with public fields only.

`--out dist` builds a complete site (static files plus data) in another folder
instead. It only ever wipes folders it created itself.

## Deploy (Cloudflare Pages, direct upload)

Published data is never committed to git. Deploying publishes approved
stories from **production**, builds the full site in `dist/`, and uploads it
with Wrangler. It refuses to run unless the publish login points at production.

One-time setup:

1. Set up the production database:
   ```
   uv run --env-file .env.production python migrations/migrate.py
   uv run python migrations/logins.py --env-file .env.production
   ```
2. Create a free Cloudflare account, then log in and create the Pages project:
   ```
   npx wrangler@4.155.0 login
   npx wrangler@4.155.0 pages project create kidnews-earth --production-branch main
   ```
   (Use another project name if that one is taken, and set
   `CLOUDFLARE_PAGES_PROJECT` to it.)

Deploy:

```
uv run --env-file .env.production python -m publish.deploy               # publish + upload
uv run --env-file .env.production python -m publish.deploy --build-only  # build dist/ only
```

The site is served at `https://<project>.pages.dev`.

Running it again is safe; it always rebuilds the full published set.

## Sample drafts for dev (`scripts/`)

Until the agent exists, add four sample drafts to the dev database:

```
uv run python -m scripts.seed_dev
```

It uses `save_draft` with the agent login, skips stories already there, and
refuses to run against production.

## Agent (`agent/`)

Makes one draft story a day from NASA Earth Observatory.

```
uv run python -m agent.run --dry-run   # fetch, write, check, print; save nothing
uv run python -m agent.run             # same, then save a draft (dev database)
```

1. **Fetch** (`nasa_eo.py`): the Image of the Day feed at
   `science.nasa.gov/feed/earth-observatory/image-of-the-day` (Earth
   Observatory moved there in December 2025). The feed includes each article's
   HTML; the fetcher keeps the story paragraphs, the NASA-hosted main image and
   its description, and the credit line. Images credited to someone else
   (©, copyright, "courtesy of" a non-NASA source) are dropped.
2. **Pick** the newest article not already stored (no model call for repeats).
3. **Write** the story (`StoryOutput`) and then the quiz (`Quiz`) with Claude
   through the `final_answer` tool (`structured.py`, prompts in `prompts.py`).
   The article goes in as untrusted source material inside `<article>` tags.
4. **Check** (`core/checks.py`): wording like "according to NASA" gets one
   retry, then the run stops without saving. A reading level above grade 9.5
   gets one retry, then is saved with a warning for the reviewer.
5. **Save** with `save_draft` (agent login). A person approves on the admin page.

The run stops if its model spend passes `DAILY_SPEND_CAP_USD` (default $0.25).
`LLM_MODEL` picks the model (default `anthropic/claude-haiku-5-5`).

## Automation (GitHub Actions)

| Workflow | When | What |
|---|---|---|
| `daily.yml` (Daily story) | Every day at 12:00 UTC, or **Run workflow** in the Actions tab | `publish` job: publishes approved stories and deploys the site. `draft` job: drafts the newest article. |
| `ci.yml` (CI) | Every push to `main` and every pull request | Ruff lint and format check, then the tests (database tests are skipped in CI). |

Each daily job gets only the secrets it needs:

| Job | Secrets |
|---|---|
| `publish` | `PUBLISH_DATABASE_URL`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` |
| `draft` | `AGENT_DATABASE_URL`, `ANTHROPIC_API_KEY` |

The owner and admin logins are never stored in GitHub. Approving drafts stays
on your laptop:

```
uv run --env-file .env.production python -m admin
```

A day in production: the 12:00 UTC run publishes whatever you approved since
the last run and drafts a new story; you review it on the admin page; the next
run publishes it. To publish right away, run the workflow by hand with
`task: publish`.

Scheduled runs only happen from `main`. Actions are pinned to exact commits.
