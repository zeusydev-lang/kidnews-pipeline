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
    def install(data=b"\xff\xd8fake-jpeg", content_type="image/jpeg", error=None):
        def urlopen(request, timeout):
            if error:
                raise error
            return FakeResponse(data, content_type)

        monkeypatch.setattr(build.urllib.request, "urlopen", urlopen)

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
