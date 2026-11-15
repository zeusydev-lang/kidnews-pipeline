"""Image downloads in the publish script, with a fake network. No database needed."""

import io
from email.message import Message

import pytest

from publish import build


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, content_type: str):
        super().__init__(data)
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_net(monkeypatch):
    requested: list[str] = []  # every URL the code asked for

    def install(data=b"\xff\xd8fake-jpeg", content_type="image/jpeg", error=None):
        def urlopen(request, timeout):
            requested.append(request.full_url)
            if error:
                raise error
            return FakeResponse(data, content_type)

        monkeypatch.setattr(build.urllib.request, "urlopen", urlopen)
        return requested

    return install


def test_image_is_saved_locally(tmp_path, fake_net):
    fake_net()
    path = build.download_image("https://example.com/a.jpg", "story-1", tmp_path)
    assert path == "images/stories/story-1.jpg"
    assert (tmp_path / "story-1.jpg").read_bytes() == b"\xff\xd8fake-jpeg"


def test_http_image_is_refused(tmp_path, fake_net):
    fake_net()
    assert build.download_image("http://example.com/a.jpg", "story-1", tmp_path) is None


@pytest.mark.parametrize(
    "content_type", ["text/html", "image/svg+xml", "application/octet-stream"]
)
def test_non_image_is_refused(tmp_path, fake_net, content_type):
    fake_net(content_type=content_type)
    assert build.download_image("https://example.com/a", "story-1", tmp_path) is None


def test_huge_image_is_refused(tmp_path, fake_net):
    fake_net(data=b"x" * (build.MAX_IMAGE_BYTES + 1))
    assert (
        build.download_image("https://example.com/a.jpg", "story-1", tmp_path) is None
    )


def test_network_error_means_no_image(tmp_path, fake_net):
    fake_net(error=OSError("timed out"))
    assert (
        build.download_image("https://example.com/a.jpg", "story-1", tmp_path) is None
    )


def test_non_ascii_url_is_percent_encoded(tmp_path, fake_net):
    # The 2026-11-15 publish run crashed on a NASA image path with a curly
    # apostrophe: urllib only sends ASCII.
    requested = fake_net()
    url = "https://assets.science.nasa.gov/content/Scotland’s_Flow_Country.jpg"
    path = build.download_image(url, "story-1", tmp_path)
    assert path == "images/stories/story-1.jpg"
    assert requested == [
        "https://assets.science.nasa.gov/content/Scotland%E2%80%99s_Flow_Country.jpg"
    ]
    requested[0].encode("ascii")  # what urllib needs


def test_existing_escapes_and_query_are_kept():
    url = "https://example.com/a%20b/c.jpg?w=800&h=600"
    assert build.ascii_url(url) == url


def test_unencodable_url_skips_the_image_instead_of_failing(tmp_path, fake_net):
    fake_net(error=UnicodeEncodeError("ascii", "x", 0, 1, "ordinal not in range"))
    assert (
        build.download_image("https://example.com/a.jpg", "story-1", tmp_path) is None
    )
