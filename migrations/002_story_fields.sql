-- 002_story_fields.sql
-- New story fields, the 'published' status, status-change rules and quiz size check.
-- Safe to run more than once. Apply with: uv run python migrations/migrate.py

-- 1. New columns on stories ------------------------------------------------

alter table stories
    add column if not exists kid_title      text,
    add column if not exists body           text,
    add column if not exists reading_level  numeric(4, 1),  -- grade level computed in code, e.g. 7.2
    add column if not exists topic          text,
    add column if not exists image_url      text,
    add column if not exists slug           text,           -- web address part, e.g. melting-glaciers-greenland
    add column if not exists model          text,           -- Claude model that wrote the story
    add column if not exists prompt_version text,
    add column if not exists source_text    text,           -- private: never published
    add column if not exists review_notes   text,           -- private: reject reason
    add column if not exists published_at   timestamptz;

-- Two stories can't share a web address.
create unique index if not exists stories_slug_key on stories (slug);

-- 2. Allowed statuses: add 'published' -------------------------------------

alter table stories drop constraint if exists stories_status_check;
alter table stories add constraint stories_status_check
    check (status in ('draft', 'approved', 'rejected', 'published'));

-- 3. What each status requires ---------------------------------------------

-- Replaces the old rule from 001 (approved needs approved_at and summary).
alter table stories drop constraint if exists stories_check;

alter table stories drop constraint if exists stories_ready_check;
alter table stories add constraint stories_ready_check
    check (
        status not in ('approved', 'published')
        or (approved_at is not null and kid_title is not null
            and summary is not null and body is not null and slug is not null)
    );

alter table stories drop constraint if exists stories_published_check;
alter table stories add constraint stories_published_check
    check (status <> 'published' or published_at is not null);

alter table stories drop constraint if exists stories_rejected_check;
alter table stories add constraint stories_rejected_check
    check (status <> 'rejected' or length(trim(coalesce(review_notes, ''))) > 0);

-- 4. Status-change rule ----------------------------------------------------
-- New stories always start as 'draft'. Only these changes are allowed:
--   draft -> approved, draft -> rejected, approved -> published
-- The approval and publish times are filled in automatically.

create or replace function stories_status_guard() returns trigger
language plpgsql as $$
begin
    if tg_op = 'INSERT' then
        if new.status <> 'draft' then
            raise exception 'new stories must start as draft, not %', new.status
                using errcode = 'check_violation';
        end if;
        return new;
    end if;

    if new.status is distinct from old.status then
        if not (
            (old.status = 'draft'    and new.status in ('approved', 'rejected'))
         or (old.status = 'approved' and new.status = 'published')
        ) then
            raise exception 'invalid status change: % -> %', old.status, new.status
                using errcode = 'check_violation';
        end if;

        if new.status = 'approved' then
            new.approved_at := now();
        elsif new.status = 'published' then
            new.published_at := now();
        end if;
    end if;

    return new;
end;
$$;

drop trigger if exists stories_status_guard on stories;
create trigger stories_status_guard
    before insert or update on stories
    for each row execute function stories_status_guard();

-- 5. Quiz size: 4 to 5 questions -------------------------------------------

alter table quizzes drop constraint if exists quizzes_question_count_check;
alter table quizzes add constraint quizzes_question_count_check
    check (
        case when jsonb_typeof(questions) = 'array'
             then jsonb_array_length(questions) between 4 and 5
             else false
        end
    );
