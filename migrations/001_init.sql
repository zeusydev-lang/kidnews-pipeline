-- 001_init.sql
-- First schema for the kidnews pipeline: publishers, stories, quizzes.
-- Safe to run more than once. Apply with: uv run python migrations/migrate.py

-- Where articles come from, and the license terms we checked.
create table if not exists publishers (
    id           bigint generated always as identity primary key,
    name         text not null unique,
    base_url     text not null,
    license_note text not null,
    created_at   timestamptz not null default now()
);

-- One row per source article, plus the AI-written story for it.
-- Only 'approved' rows get published.
create table if not exists stories (
    id           bigint generated always as identity primary key,
    publisher_id bigint not null references publishers (id),
    source_url   text not null,
    title        text not null,
    author       text,
    image_credit text,
    published_on date,
    fetched_on   timestamptz not null default now(),
    text_hash    text not null,              -- detects if the article text changed
    summary      text,                       -- empty until the summarize step runs
    concepts     jsonb,
    glossary     jsonb,
    grade_band   text not null default '6-8',
    status       text not null default 'draft'
                 check (status in ('draft', 'approved', 'rejected')),
    approved_at  timestamptz,
    unique (publisher_id, source_url),       -- dedupe: same article is never stored twice
    check (status <> 'approved' or (approved_at is not null and summary is not null))
);

-- One quiz per story. Questions are stored as a JSON array:
-- [{"prompt": "...", "options": ["a","b","c","d"], "correct_index": 0, "explanation": "..."}]
create table if not exists quizzes (
    id         bigint generated always as identity primary key,
    story_id   bigint not null unique references stories (id) on delete cascade,
    questions  jsonb not null check (jsonb_typeof(questions) = 'array'),
    created_at timestamptz not null default now()
);

-- Seed the one publisher for the first release.
insert into publishers (name, base_url, license_note)
values (
    'NASA Earth Observatory',
    'https://earthobservatory.nasa.gov',
    'NASA content is generally not copyrighted in the US; acknowledge NASA as the source. '
    'Check each image credit line for third-party copyright. Do not imply NASA endorsement.'
)
on conflict (name) do nothing;
