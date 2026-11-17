"""NASA Earth Observatory feed parsing. No network: uses a small sample feed
built like the real one (including NASA's missing-space XML bug)."""

from datetime import date

import pytest

from agent import nasa_eo
from agent.nasa_eo import SourceError, parse_feed


def item(
    title="Sea Ice Shrinks",
    slug="sea-ice-shrinks",
    paragraphs=("Sea ice grows in winter.", "It melts in summer."),
    credit="NASA Earth Observatory images by Jane Doe. Story by John Roe.",
    image=(
        '<img width="800" src="https://assets.science.nasa.gov/content/dam/ice.jpg"'
        ' alt="White ice on a blue sea.">'
    ),
    link=None,
) -> str:
    body = "".join(f'<p class="wp-block-paragraph">{p}</p>' for p in paragraphs)
    if credit:
        body += f'<p class="wp-block-paragraph">{credit}</p>'
    content = (
        '<div class="nav"><p>ScienceEarth ObservatoryMenu'  # unclosed nav <p>
        '<img src="https://assets.science.nasa.gov/dynamicimage/x.jpg" alt="">'
        f"{image}{body}"
        "<p>The post appeared first on NASA Science.</p></div>"
    )
    link = link or f"https://science.nasa.gov/earth/earth-observatory/{slug}/"
    return f"""
    <item>
      <title>{title}</title>
      <link>{link}</link>
      <dc:creator><![CDATA[Jane Doe]]></dc:creator>
      <pubDate>Fri, 13 Nov 2026 05:01:00 +0000</pubDate>
      <category><![CDATA[Earth Observatory]]></category>
      <category><![CDATA[Sea and Lake Ice]]></category>
      <description><![CDATA[<p>Arctic ice is changing.</p><p>Post</p>]]></description>
      <content:encoded><![CDATA[{content}]]></content:encoded>
    </item>"""


def feed(*items: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"
    xmlns:content="http://purl.org/rss/1.0/modules/content/"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:apod="https://x.test/apod/"xmlns:media="http://search.yahoo.com/mrss/" >
    <channel><title>NASA Science</title>{"".join(items)}</channel></rss>"""


class TestParseFeed:
    def test_repairs_nasa_xml_and_reads_an_article(self):
        [article] = parse_feed(feed(item()))
        assert article.title == "Sea Ice Shrinks"
        assert article.url.endswith("/sea-ice-shrinks/")
        assert article.author == "Jane Doe"
        assert article.published_on == date(2026, 11, 13)
        assert article.summary == "Arctic ice is changing."
        assert article.categories == ["Sea and Lake Ice"]

    def test_body_is_story_paragraphs_only(self):
        [article] = parse_feed(feed(item()))
        assert article.text == "Sea ice grows in winter.\n\nIt melts in summer."
        assert "Menu" not in article.text  # nav text left out
        assert "Story by" not in article.text  # credit kept separately

    def test_credit_line(self):
        [article] = parse_feed(feed(item()))
        assert article.image_credit.startswith("NASA Earth Observatory images by")

    def test_astronaut_photo_credit(self):
        credit = "Astronaut photograph ISS075-E-1 was acquired on October 6, 2026."
        [article] = parse_feed(feed(item(credit=credit)))
        assert article.image_credit == credit
        assert "Astronaut" not in article.text

    def test_long_astronaut_credit_is_shortened(self):
        # Real credit from 2026-11-17 ("Tossing Fireworks from Cloud to Cloud").
        credit = (
            "Astronaut photograph ISS075-E-250951 was acquired on October 28, 2026,"
            " with a Nikon Z9 digital camera using a focal length of 14 millimeters."
            " It is provided by the ISS Crew Earth Observations Facility and the"
            " Earth Science and Remote Sensing Unit at NASA Johnson Space Center."
            " The image was taken by a member of the Expedition 75 crew. The image"
            " has been cropped and enhanced to improve contrast, and lens artifacts"
            " have been removed. The International Space Station Program supports"
            " the laboratory as part of the ISS National Lab to help astronauts take"
            " pictures of Earth that will be of the greatest value to scientists and"
            " the public, and to make those images freely available on the Internet."
            " Additional images taken by astronauts and cosmonauts can be viewed at"
            " the NASA/JSC Gateway to Astronaut Photography of Earth."
            " Story by Adam Voiland."
        )
        [article] = parse_feed(feed(item(credit=credit)))
        assert article.image_credit == (
            "Astronaut photograph ISS075-E-250951 was acquired on October 28, 2026,"
            " with a Nikon Z9 digital camera using a focal length of 14 millimeters."
            " Story by Adam Voiland."
        )

    def test_credit_never_exceeds_300_chars(self):
        credit = "Images by " + "Someone " * 60 + "and more."
        [article] = parse_feed(feed(item(credit=credit)))
        assert len(article.image_credit) <= 300
        assert article.image_credit.endswith("…")

    def test_main_image_is_first_nasa_image_with_a_description(self):
        [article] = parse_feed(feed(item()))
        assert (
            article.image_url == "https://assets.science.nasa.gov/content/dam/ice.jpg"
        )
        assert article.image_alt == "White ice on a blue sea."

    def test_non_nasa_image_host_is_ignored(self):
        other = '<img src="https://example.com/ice.jpg" alt="Ice">'
        [article] = parse_feed(feed(item(image=other)))
        assert article.image_url is None

    def test_copyrighted_image_is_dropped_but_story_kept(self):
        credit = "Photograph © 2026 Someone Else. All rights reserved."
        [article] = parse_feed(feed(item(credit=credit)))
        assert article.image_url is None and article.image_alt is None
        assert article.text

    def test_html_entities_and_tags_are_cleaned(self):
        paragraphs = ["Burma&#8217;s <a href='x'>Landsat</a>   image &amp; more."]
        [article] = parse_feed(feed(item(paragraphs=paragraphs)))
        assert article.text == "Burma’s Landsat image & more."

    def test_items_outside_earth_observatory_are_skipped(self):
        links = [
            "https://example.com/earth-observatory/fake/",
            "https://science.nasa.gov/missions/other/",
        ]
        assert parse_feed(feed(*(item(link=link) for link in links))) == []

    def test_items_without_body_are_skipped(self):
        assert parse_feed(feed(item(paragraphs=[]))) == []

    def test_keeps_feed_order(self):
        articles = parse_feed(feed(item(slug="newest"), item(slug="older")))
        assert [a.url.split("/")[-2] for a in articles] == ["newest", "older"]

    def test_text_hash_changes_with_text(self):
        a = parse_feed(feed(item()))[0]
        b = parse_feed(feed(item(paragraphs=["Different text."])))[0]
        assert len(a.text_hash) == 64 and a.text_hash != b.text_hash

    def test_broken_xml_raises_source_error(self):
        with pytest.raises(SourceError):
            parse_feed("<rss><channel><item>")


class TestFetch:
    def test_network_error_becomes_source_error(self, monkeypatch):
        def fail(request, timeout):
            raise OSError("no network")

        monkeypatch.setattr(nasa_eo.urllib.request, "urlopen", fail)
        with pytest.raises(SourceError, match="Couldn't fetch"):
            nasa_eo.fetch_feed()


@pytest.mark.parametrize(
    "credit",
    [
        "Photo courtesy of Example University.",
        "Image credit: Someone Else, used with permission. Copyright 2026.",
    ],
)
def test_other_third_party_credits_drop_the_image(credit):
    [article] = parse_feed(feed(item(credit=credit)))
    assert article.image_url is None
    assert credit not in article.text


def test_courtesy_of_nasa_keeps_the_image():
    credit = "Image courtesy of NASA's Scientific Visualization Studio."
    [article] = parse_feed(feed(item(credit=credit)))
    assert article.image_url is not None
