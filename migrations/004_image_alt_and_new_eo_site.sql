-- 004_image_alt_and_new_eo_site.sql
-- 1. NASA Earth Observatory moved to science.nasa.gov in December 2025.
-- 2. Stories get an image description (alt text) for screen readers.
-- Safe to run more than once. Apply with: uv run python migrations/migrate.py

update publishers
   set base_url = 'https://science.nasa.gov/earth/earth-observatory'
 where name = 'NASA Earth Observatory'
   and base_url <> 'https://science.nasa.gov/earth/earth-observatory';

alter table stories add column if not exists image_alt text;

-- The agent may write the image description too.
grant insert (image_alt) on stories to kidnews_agent;

-- The public view (moved here from 003): public fields only, no source_text,
-- review_notes, text_hash or model details. New columns go at the end so
-- create-or-replace keeps the existing column order.
create or replace view public_stories as
select
    s.id,
    s.slug,
    s.kid_title,
    s.summary,
    s.body,
    s.concepts,
    s.glossary,
    s.topic,
    s.reading_level,
    s.grade_band,
    s.image_url,
    s.image_credit,
    s.title        as source_title,
    s.source_url,
    s.author       as source_author,
    s.published_on as source_published_on,
    p.name         as publisher_name,
    s.status,
    s.approved_at,
    s.published_at,
    q.questions,
    s.image_alt
from stories s
join publishers p on p.id = s.publisher_id
left join quizzes q on q.story_id = s.id
where s.status in ('approved', 'published');

revoke all on public_stories from public;
grant select on public_stories to kidnews_publish;
