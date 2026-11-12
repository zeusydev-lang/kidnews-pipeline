"""Add a few SAMPLE draft stories to the dev database.

Stands in for the agent until it exists, so the admin page and publish
script have drafts to work with. Uses save_draft with the agent login,
the same path the real agent will use.

Refuses to run if the agent login points at the production database.
Running it twice is safe: stories that already exist are skipped.

Usage:
    uv run python -m scripts.seed_dev
"""

import sys
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

from core import stories
from core.db import connect
from core.models import StoryDraft
from core.settings import database_url

ROOT = Path(__file__).resolve().parent.parent


def q(prompt, options, correct, explanation):
    return {"prompt": prompt, "options": options, "correct_index": correct, "explanation": explanation}


SAMPLES = [
    {
        "slug": "sample-why-sea-ice-shrinks",
        "kid_title": "Sample: Why Does Sea Ice Shrink in Summer?",
        "topic": "Ice and snow",
        "reading_level": 6.8,
        "summary": "SAMPLE STORY. Sea ice near the North Pole grows in winter and shrinks in summer. "
                   "Scientists use satellites to measure how much is left each year.",
        "body": "The Arctic Ocean is the sea around the North Pole. In winter, the air is so cold that the "
                "top of the ocean freezes into a thick layer of sea ice.\n\n"
                "When summer comes, the Sun shines for many hours each day. The ice soaks up some of that "
                "energy and starts to melt. By September, there is much less ice than in March.\n\n"
                "Satellites take pictures of the Arctic every day. By comparing pictures from many years, "
                "scientists can tell whether the summer ice is getting smaller over time.",
        "concepts": [{"name": "Albedo", "explanation": "How much sunlight a surface bounces back."}],
        "glossary": [{"term": "Arctic", "definition": "The cold region around the North Pole."}],
        "questions": [
            q("When does Arctic sea ice grow the most?", ["Summer", "Winter", "Spring", "It never grows"], 1,
              "The ocean freezes in winter, when the air is very cold."),
            q("What do scientists use to watch the ice every day?",
              ["Boats", "Weather balloons", "Satellites", "Submarines"], 2,
              "Satellites take pictures of the Arctic every day."),
            q("What does 'albedo' describe?",
              ["How cold water is", "How much sunlight a surface bounces back", "How thick ice is",
               "How fast wind blows"], 1, "Albedo is how much sunlight a surface reflects."),
            q("By which month is there much less ice than in March?",
              ["January", "June", "September", "December"], 2, "By September there is much less ice."),
        ],
    },
    {
        "slug": "sample-forests-clean-air",
        "kid_title": "Sample: How Forests Help Clean the Air",
        "topic": "Forests",
        "reading_level": 6.2,
        "summary": "SAMPLE STORY. Trees take in carbon dioxide and give off oxygen. "
                   "Satellites can see how green a forest is from space.",
        "body": "Trees use sunlight, water and carbon dioxide from the air to make their own food. "
                "This is called photosynthesis.\n\n"
                "While they do this, trees give off oxygen, the gas we need to breathe.",
        "concepts": [{"name": "Photosynthesis", "explanation": "How plants use sunlight to make food."}],
        "glossary": [],
        "questions": [
            q("What gas do trees take in from the air?", ["Oxygen", "Carbon dioxide", "Helium", "Steam"], 1,
              "Trees take in carbon dioxide to make food."),
            q("What is photosynthesis?", ["How plants make food from sunlight", "How rain forms",
                                          "How trees grow roots", "How leaves fall"], 0,
              "Photosynthesis is how plants use sunlight to make food."),
            q("What gas do trees give off?", ["Carbon dioxide", "Smoke", "Oxygen", "Nitrogen"], 2,
              "Trees give off oxygen while they make food."),
            q("What three things do trees use to make food?",
              ["Sunlight, water, carbon dioxide", "Soil, rocks, wind", "Snow, ice, salt", "Oxygen, smoke, sand"],
              0, "The story lists sunlight, water and carbon dioxide."),
        ],
    },
    {
        "slug": "sample-volcano-from-space",
        "kid_title": "Sample: Watching a Volcano from Space",
        "topic": "Volcanoes",
        "reading_level": 7.1,
        "summary": "SAMPLE STORY. When a volcano erupts, satellites can spot the hot lava and the cloud of ash "
                   "from far above Earth.",
        "body": "A volcano is an opening in Earth's crust where melted rock, called magma, comes up from deep "
                "underground. When magma reaches the surface, we call it lava.\n\n"
                "Some satellites carry cameras that can sense heat. Hot lava glows brightly in these images, "
                "even at night. Other cameras can see the ash cloud drifting with the wind.\n\n"
                "Watching from space helps keep airplanes safe, because ash can damage jet engines.",
        "concepts": [{"name": "Magma and lava", "explanation": "Magma is melted rock underground. "
                                                              "Once it reaches the surface it is called lava."}],
        "glossary": [{"term": "Crust", "definition": "The thin, rocky outer layer of Earth."}],
        "questions": [
            q("What is lava?", ["Cooled ash", "Magma that reaches the surface", "A kind of cloud",
                                "Water under a volcano"], 1, "Magma is called lava once it reaches the surface."),
            q("Why does lava show up in heat images at night?",
              ["It is very hot", "It reflects moonlight", "It is painted", "It is made of ice"], 0,
              "Cameras that sense heat see hot lava glowing."),
            q("Why is watching ash clouds important?", ["Ash makes rainbows", "Ash can damage jet engines",
                                                        "Ash cools the ocean", "Ash grows trees"], 1,
              "The story says ash can damage jet engines."),
            q("What is Earth's crust?", ["The hot center", "The thin, rocky outer layer", "The air above us",
                                         "The ocean floor only"], 1, "The crust is Earth's thin outer layer."),
        ],
    },
    {
        "slug": "sample-dust-across-the-ocean",
        "kid_title": "Sample: Dust That Crosses an Ocean",
        "topic": "Weather",
        "reading_level": 7.4,
        "summary": "SAMPLE STORY. Strong winds can lift dust from deserts and carry it thousands of kilometers "
                   "across the ocean.",
        "body": "Deserts are full of fine dust. When strong winds blow, they can lift huge clouds of it high "
                "into the sky.\n\n"
                "Some of this dust travels across the Atlantic Ocean. Satellites see it as a tan haze over the "
                "blue water. When it lands, the dust can even bring nutrients to faraway forests.",
        "concepts": [{"name": "Nutrients", "explanation": "Things living things need to grow, like minerals."}],
        "glossary": [{"term": "Haze", "definition": "A thin cloud of dust or smoke that makes the sky look blurry."}],
        "questions": [
            q("Where does the dust come from?", ["Forests", "Deserts", "Glaciers", "Cities"], 1,
              "The dust is lifted from deserts."),
            q("What lifts the dust into the sky?", ["Rain", "Strong winds", "Earthquakes", "Ocean waves"], 1,
              "Strong winds lift the dust."),
            q("How does the dust look from a satellite?", ["Green spots", "A tan haze", "White snow",
                                                           "Bright lights"], 1,
              "Satellites see a tan haze over the water."),
            q("How can the dust help faraway forests?", ["It brings nutrients", "It makes shade",
                                                         "It stops rain", "It scares insects"], 0,
              "The dust can bring nutrients when it lands."),
        ],
    },
]


def refuse_if_production() -> None:
    prod = ROOT / ".env.production"
    prod_url = dotenv_values(prod).get("DATABASE_URL") if prod.exists() else None
    if prod_url and urlsplit(prod_url).hostname == urlsplit(database_url("agent")).hostname:
        sys.exit("Refusing to add sample stories: AGENT_DATABASE_URL points at production.")


def main() -> int:
    refuse_if_production()
    with connect("agent") as conn:
        publisher_id = conn.execute(
            "select id from publishers where name = 'NASA Earth Observatory'"
        ).fetchone()["id"]

    for n, s in enumerate(SAMPLES, start=1):
        source_url = f"https://example.com/sample-article-{n}"
        if stories.find_existing(publisher_id, source_url):
            print(f"  skip  {s['slug']} (already there)")
            continue
        draft = StoryDraft(
            publisher_id=publisher_id,
            source_url=source_url,
            title=f"Sample source article {n} (not real)",
            text_hash=f"sample-{n}",
            source_text="SAMPLE SOURCE TEXT. Not a real article.",
            image_credit="Sample: no image",
            model="sample-data",
            prompt_version="sample",
            quiz={"questions": s["questions"]},
            **{k: v for k, v in s.items() if k != "questions"},
        )
        story_id = stories.save_draft(draft)
        print(f"  added {s['slug']} (id {story_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
