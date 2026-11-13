"""Content checks: reading level and NASA attribution wording."""

import pytest

from core.checks import attribution_problems, count_syllables, reading_level


class TestReadingLevel:
    def test_simple_text_is_low(self):
        assert reading_level("The cat sat. It was warm. The sun was out.") < 3

    def test_middle_school_text(self):
        text = (
            "Sea ice is frozen ocean water. In winter, the air near the North Pole "
            "gets very cold, so the top of the ocean freezes. Scientists use "
            "satellites to measure how much ice is left at the end of each summer."
        )
        assert 3 <= reading_level(text) <= 9

    def test_academic_text_is_high(self):
        text = (
            "Substantial anthropogenic perturbations of biogeochemical cycles have "
            "precipitated unprecedented transformations in coastal ecosystems."
        )
        assert reading_level(text) > 14

    def test_empty_text(self):
        assert reading_level("") == 0.0

    @pytest.mark.parametrize(
        ("word", "syllables"),
        [("cat", 1), ("water", 2), ("satellite", 3), ("ocean", 2), ("the", 1)],
    )
    def test_syllables(self, word, syllables):
        assert count_syllables(word) == syllables


class TestAttribution:
    @pytest.mark.parametrize(
        "text",
        [
            "According to NASA, the ice is melting.",
            "NASA says the ice is thin.",
            "This story was reviewed by NASA.",
            "NASA scientists say it is rare.",
        ],
    )
    def test_forbidden_wording_is_found(self, text):
        assert attribution_problems(text)

    @pytest.mark.parametrize(
        "text",
        [
            "A NASA satellite took this image.",
            "Scientists found that mangroves are growing.",
            "The Landsat 9 satellite is run by NASA and the USGS.",
        ],
    )
    def test_allowed_wording(self, text):
        assert attribution_problems(text) == []

    def test_checks_all_texts(self):
        assert attribution_problems("Fine.", "Also fine.", "nasa says hi") == [
            "nasa says"
        ]


def test_reading_level_is_capped_at_20():
    text = (
        "Substantial anthropogenic perturbations of biogeochemical cycles have "
        "precipitated unprecedented transformations in coastal ecosystems."
    )
    assert reading_level(text) == 20.0
