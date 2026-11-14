"""NASA Earth Observatory source: fetch the Image of the Day feed and pull out
each article's text, image and credit (build plan task 11).

Earth Observatory moved to science.nasa.gov in December 2025. Its feed
includes the full article HTML, so one request gives us everything.

Fetched text is untrusted: it is only ever passed to the model as data inside
the prompt, never as instructions.

    articles = fetch_articles()     # newest first
    article = articles[0]
    article.text, article.image_url, article.image_credit
"""

import hashlib
import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from email.utils import parsedate_to_datetime

from pydantic import BaseModel

FEED_URL = "https://science.nasa.gov/feed/earth-observatory/image-of-the-day"
USER_AGENT = "kidnews-pipeline/1.0 (educational project)"
MAX_FEED_BYTES = 10 * 1024 * 1024
ALLOWED_ARTICLE_PREFIX = "https://science.nasa.gov/earth/earth-observatory/"
ALLOWED_IMAGE_PREFIX = "https://assets.science.nasa.gov/"

NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

# Paragraphs that are the credit line, not the story.
CREDIT_PATTERN = re.compile(
    r"\b(images?|photographs?|photos?|maps?|animation|video"
    r"|data visuali[sz]ation) by\b"
    r"|\bStory by\b|^Astronaut photograph\b"
    r"|©|\bcopyright\b|\bcourtesy of\b|^(image|photo|photograph)s? credit",
    re.IGNORECASE,
)
# Signs an image belongs to someone else and isn't free to reuse.
THIRD_PARTY_PATTERN = re.compile(
    r"©|\bcopyright\b|all rights reserved|\bcourtesy of (?!nasa\b)",
    re.IGNORECASE,
)


class SourceError(Exception):
    """The feed couldn't be fetched or read."""


class Article(BaseModel):
    title: str
    url: str
    author: str | None = None
    published_on: date | None = None
    summary: str = ""
    text: str  # body paragraphs separated by blank lines
    image_url: str | None = None
    image_alt: str | None = None
    image_credit: str | None = None
    categories: list[str] = []

    @property
    def text_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def word_count(self) -> int:
        return len(self.text.split())


# --- Fetching ----------------------------------------------------------------


def fetch_feed(url: str = FEED_URL, timeout: float = 30.0) -> str:
    """Download the feed as text. Plain HTTP, so the full article comes through."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_FEED_BYTES + 1)
    except OSError as err:
        raise SourceError(f"Couldn't fetch the feed ({type(err).__name__})") from err
    if len(data) > MAX_FEED_BYTES:
        raise SourceError("Feed is unexpectedly large")
    return data.decode("utf-8", errors="replace")


def fetch_articles(url: str = FEED_URL) -> list[Article]:
    """Fetch and parse the feed. Newest article first."""
    return parse_feed(fetch_feed(url))


# --- Parsing -----------------------------------------------------------------


def _repair_xml(text: str) -> str:
    """NASA's feed is missing a space between two xmlns attributes
    (`..."xmlns:media=...`), which strict XML parsers reject."""
    return re.sub(r'"(xmlns:)', r'" \1', text)


def _clean(fragment: str) -> str:
    """HTML fragment -> plain text with normal spacing."""
    text = re.sub(r"<[^>]+>", "", fragment)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _date(value: str | None) -> date | None:
    try:
        return parsedate_to_datetime(value).date() if value else None
    except (TypeError, ValueError):
        return None


def parse_feed(feed_xml: str) -> list[Article]:
    """Parse feed XML into Articles. Items that can't be used are skipped."""
    try:
        root = ET.fromstring(_repair_xml(feed_xml))
    except ET.ParseError as err:
        raise SourceError(f"Feed isn't valid XML: {err}") from err
    channel = root.find("channel")
    if channel is None:
        raise SourceError("Feed has no channel")

    articles = []
    for item in channel.findall("item"):
        article = parse_item(item)
        if article is not None:
            articles.append(article)
    return articles


def parse_item(item: ET.Element) -> Article | None:
    title = _clean(item.findtext("title") or "")
    url = (item.findtext("link") or "").strip()
    content = item.findtext("content:encoded", namespaces=NS) or ""
    if not title or not url.startswith(ALLOWED_ARTICLE_PREFIX) or not content:
        return None

    paragraphs = [
        _clean(p)
        for p in re.findall(
            r'<p class="wp-block-paragraph"[^>]*>(.*?)</p>', content, re.DOTALL
        )
    ]
    paragraphs = [p for p in paragraphs if p]
    credits = [p for p in paragraphs if CREDIT_PATTERN.search(p)]
    body = [p for p in paragraphs if p not in credits]
    if not body:
        return None

    image_credit = credits[-1] if credits else None
    image_url, image_alt = _main_image(content)
    if any(THIRD_PARTY_PATTERN.search(c) for c in credits):
        image_url = image_alt = None  # not NASA's to share; keep the story only

    description = item.findtext("description") or ""
    summary_match = re.search(r"<p>(.*?)</p>", description, re.DOTALL)

    return Article(
        title=title,
        url=url,
        author=_clean(item.findtext("dc:creator", namespaces=NS) or "") or None,
        published_on=_date(item.findtext("pubDate")),
        summary=_clean(summary_match.group(1)) if summary_match else "",
        text="\n\n".join(body),
        image_url=image_url,
        image_alt=image_alt,
        image_credit=image_credit,
        categories=[
            _clean(c.text or "")
            for c in item.findall("category")
            if c.text and _clean(c.text) != "Earth Observatory"
        ],
    )


def _main_image(content: str) -> tuple[str | None, str | None]:
    """First NASA-hosted image that has a description."""
    for tag in re.findall(r"<img\b[^>]*>", content):
        src = re.search(r'\bsrc="([^"]+)"', tag)
        alt = re.search(r'\balt="([^"]*)"', tag)
        if not src or not alt:
            continue
        url, description = html.unescape(src.group(1)), _clean(alt.group(1))
        if url.startswith(ALLOWED_IMAGE_PREFIX) and description:
            return url, description
    return None, None
