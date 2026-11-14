-- 005_eval_runs.sql
-- One row per check per story: reading level, attribution, glossary (code
-- checks) and grounding, quiz answerability, safety, tone (the judge).
-- Pass rates for the README come from this table.
-- Safe to run more than once. Apply with: uv run python migrations/migrate.py

create table if not exists eval_runs (
    id           bigint generated always as identity primary key,
    story_id     bigint not null references stories (id) on delete cascade,
    check_name   text not null,
    passed       boolean not null,
    details      jsonb not null default '{}'::jsonb,
    judge_model  text,          -- null for checks done in code
    run_at       timestamptz not null default now()
);
create index if not exists eval_runs_story_id_idx on eval_runs (story_id);

-- The agent records results with its drafts. It can't read, change or
-- delete them (003 revokes everything first on each run, so grants are
-- repeated here).
grant insert (story_id, check_name, passed, details, judge_model)
    on eval_runs to kidnews_agent;

-- The admin page and the pass-rate report read them.
grant select on eval_runs to kidnews_admin;
