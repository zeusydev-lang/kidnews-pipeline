"""Quiz placement: the model writes answers as text, code decides positions."""

import pytest
from pydantic import ValidationError

from agent.outputs import QuizItem, QuizOutput, StoryOutput, to_quiz
from agent.run import unused_glossary_terms


def item(i: int, **overrides) -> dict:
    data = {
        "prompt": f"Question {i}?",
        "correct_answer": f"Right {i}",
        "wrong_answers": [f"Wrong {i}a", f"Wrong {i}b", f"Wrong {i}c"],
        "explanation": "The story says so.",
    }
    return data | overrides


def quiz_output(n: int = 4) -> QuizOutput:
    return QuizOutput(questions=[item(i) for i in range(n)])


class TestToQuiz:
    def test_correct_index_always_points_at_the_correct_answer(self):
        for seed in ("a", "b", "c", "https://example.com/x"):
            quiz = to_quiz(quiz_output(), seed=seed)
            for i, q in enumerate(quiz.questions):
                assert q.options[q.correct_index] == f"Right {i}"

    def test_four_questions_use_each_position_once(self):
        quiz = to_quiz(quiz_output(4), seed="x")
        assert sorted(q.correct_index for q in quiz.questions) == [0, 1, 2, 3]

    def test_five_questions_start_a_new_round(self):
        quiz = to_quiz(quiz_output(5), seed="x")
        positions = [q.correct_index for q in quiz.questions]
        assert sorted(positions[:4]) == [0, 1, 2, 3]

    def test_same_seed_same_order(self):
        assert to_quiz(quiz_output(), seed="s") == to_quiz(quiz_output(), seed="s")

    def test_all_options_are_kept(self):
        q = to_quiz(quiz_output(), seed="s").questions[0]
        assert sorted(q.options) == sorted(
            ["Right 0", "Wrong 0a", "Wrong 0b", "Wrong 0c"]
        )


class TestQuizItem:
    def test_needs_exactly_three_wrong_answers(self):
        with pytest.raises(ValidationError):
            QuizItem(**item(0, wrong_answers=["a", "b"]))

    def test_correct_answer_cannot_repeat_as_a_wrong_answer(self):
        with pytest.raises(ValidationError, match="must all differ"):
            QuizItem(**item(0, wrong_answers=["right 0", "b", "c"]))

    def test_no_index_field_for_the_model(self):
        with pytest.raises(ValidationError):
            QuizItem(**item(0, correct_index=2))


def test_unused_glossary_terms():
    story = StoryOutput(
        kid_title="T",
        summary="Mangroves grow on coasts.",
        body="Sediment builds new land.",
        topic="Forests",
        concepts=[
            {"name": "A", "explanation": "a"},
            {"name": "B", "explanation": "b"},
        ],
        glossary=[
            {"term": "Mangrove", "definition": "d"},  # plural in text: counts
            {"term": "Sediment", "definition": "d"},
            {"term": "Canopy", "definition": "d"},  # not used
        ],
    )
    assert unused_glossary_terms(story) == ["Canopy"]
