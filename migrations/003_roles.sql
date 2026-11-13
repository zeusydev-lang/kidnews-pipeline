-- 003_roles.sql
-- Least-privilege database logins for each part of the pipeline.
-- Safe to run more than once. Apply with: uv run python migrations/migrate.py
--
-- Roles are created WITHOUT login here, so no passwords live in git.
-- Turn on login and set passwords with: uv run python migrations/logins.py
--
--   kidnews_agent    adds draft stories and quizzes. Cannot change or delete anything.
--   kidnews_admin    reads everything, changes only status and review_notes.
--   kidnews_publish  reads public fields of approved stories, marks them published.

-- 1. Create the roles if they don't exist ----------------------------------

do $$
declare
    r text;
begin
    foreach r in array array['kidnews_agent', 'kidnews_admin', 'kidnews_publish'] loop
        if not exists (select 1 from pg_roles where rolname = r) then
            execute format('create role %I nologin', r);
        end if;
    end loop;
end;
$$;

-- 2. Start from zero, then grant only what each role needs -----------------

revoke all on all tables in schema public from kidnews_agent, kidnews_admin, kidnews_publish;
grant usage on schema public to kidnews_agent, kidnews_admin, kidnews_publish;

-- 3. Agent: insert drafts only ---------------------------------------------
-- It cannot set status, approved_at, published_at or review_notes, so every
-- story it writes starts as 'draft'.

grant select on publishers to kidnews_agent;

grant insert (
    publisher_id, source_url, title, author, image_credit, published_on, text_hash,
    summary, concepts, glossary, grade_band,
    kid_title, body, reading_level, topic, image_url, slug, model, prompt_version, source_text
) on stories to kidnews_agent;
-- Needed to check for duplicates and to get the new id back after insert.
grant select (id, publisher_id, source_url, text_hash) on stories to kidnews_agent;

grant insert (story_id, questions) on quizzes to kidnews_agent;
grant select (id, story_id) on quizzes to kidnews_agent;

-- 4. Admin: read everything, change status and review notes ----------------
-- The status trigger from 002 still decides which changes are allowed.

grant select on publishers, stories, quizzes to kidnews_admin;
grant update (status, review_notes) on stories to kidnews_admin;

-- 5. Publish: public fields only -------------------------------------------
-- The public_stories view and its grant now live in 004 (one definition,
-- so re-running this file can't fight a newer version of the view).

-- Publish can only move approved -> published, through this function.
-- It has no direct update rights on stories.
create or replace function mark_published(p_story_id bigint) returns boolean
language sql
security definer
set search_path = public, pg_temp
as $$
    update stories
       set status = 'published'
     where id = p_story_id
       and status = 'approved'
    returning true;
$$;

revoke all on function mark_published(bigint) from public;
grant execute on function mark_published(bigint) to kidnews_publish;
