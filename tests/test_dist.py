"""Building the deployable site folder. No database needed."""

import pytest

from publish.build import DIST_MARKER, copy_static


def test_copy_static_makes_a_clean_site(tmp_path):
    dest = tmp_path / "dist"
    copy_static(dest)
    for name in (
        "index.html",
        "story.html",
        "about.html",
        "_headers",
        "css/style.css",
        "js/story.js",
    ):
        assert (dest / name).exists(), name
    assert not (dest / "data").exists()
    assert (dest / DIST_MARKER).exists()


def test_copy_static_replaces_its_own_folder(tmp_path):
    dest = tmp_path / "dist"
    copy_static(dest)
    (dest / "old.txt").write_text("stale")
    copy_static(dest)
    assert not (dest / "old.txt").exists()


def test_copy_static_refuses_to_wipe_other_folders(tmp_path):
    dest = tmp_path / "important"
    dest.mkdir()
    (dest / "keep.txt").write_text("mine")
    with pytest.raises(SystemExit):
        copy_static(dest)
    assert (dest / "keep.txt").read_text() == "mine"
