"""Tests for policy parsing, retrieval and the ``search_policies`` tool."""

from __future__ import annotations

import pytest

from citywheels.policies import MAX_CHUNK_CHARS, PolicyIndex, parse_markdown
from citywheels.tools.policies import search_policies

SAMPLE = """# Prices

All prices are in euros.

## Bike rental

| Bike | Per day |
| City bike | 20 EUR |

## Deposit

50 EUR per bike.
"""


@pytest.fixture(scope="module")
def index(data_dir):
    return PolicyIndex.from_data_dir(data_dir)


def test_index_loads_all_three_documents(index):
    assert {chunk.source for chunk in index.chunks} == {
        "prices.md",
        "cancellations.md",
        "faq.md",
    }


def test_every_chunk_has_the_required_fields(index):
    assert index.chunks
    for chunk in index.chunks:
        assert chunk.source.endswith(".md")
        assert chunk.document
        assert chunk.heading
        assert chunk.text.strip()
        assert chunk.line_start >= 1


def test_chunk_text_is_bounded(index):
    for chunk in index.chunks:
        assert len(chunk.text) <= MAX_CHUNK_CHARS + 3


def test_parser_splits_on_headings():
    chunks = parse_markdown("prices.md", SAMPLE)
    assert [chunk.heading for chunk in chunks] == ["Prices", "Bike rental", "Deposit"]
    assert chunks[0].line_start == 1
    assert chunks[1].line_start == 5
    assert chunks[1].text.startswith("| Bike |")


def test_parser_skips_empty_sections():
    text = "# Title\n## Empty\n## Full\nSome rules.\n"
    chunks = parse_markdown("doc.md", text)
    assert [chunk.heading for chunk in chunks] == ["Full"]


@pytest.mark.parametrize(
    ("question", "expected_source"),
    [
        ("How much is an e-bike for a whole day?", "prices.md"),
        ("What is the deposit for a city bike?", "prices.md"),
        ("What time do you open on Saturday?", "faq.md"),
        ("Can my 14-year-old rent an e-bike?", "faq.md"),
        ("Do you sell bikes?", "faq.md"),
        ("Can I take the bike abroad?", "faq.md"),
        ("I want to cancel booking CW-1002. How much will I get back?", "cancellations.md"),
        ("Can I change the dates of my booking?", "cancellations.md"),
        ("When does the deposit come back?", "cancellations.md"),
    ],
)
def test_example_questions_hit_the_right_document(index, question, expected_source):
    result = search_policies(question, index=index)
    assert result["found"] is True
    assert result["results"][0]["source"] == expected_source


@pytest.mark.parametrize(
    "question",
    [
        "What is the capital of Australia?",
        "How do I reset my Netflix password?",
        "zxqvbnm plorb",
    ],
)
def test_out_of_scope_questions_find_nothing(index, question):
    result = search_policies(question, index=index)
    assert result["found"] is False
    assert result["results"] == []
    assert "help@citywheels.example" in result["hint"]


def test_empty_query_is_handled(index):
    result = search_policies("   ", index=index)
    assert result["found"] is False
    assert result["message"] == "No question was provided."


def test_top_k_is_respected(index):
    assert len(index.search("bike", top_k=1)) == 1
    assert len(index.search("bike", top_k=2)) <= 2


def test_scores_are_sorted_descending(index):
    scores = [match.score for match in index.search("booking refund bike", top_k=5)]
    assert scores == sorted(scores, reverse=True)


def test_search_is_deterministic(index):
    first = [match.to_dict() for match in index.search("opening hours", top_k=3)]
    second = [match.to_dict() for match in index.search("opening hours", top_k=3)]
    assert first == second


def test_case_and_punctuation_do_not_matter(index):
    plain = search_policies("opening hours", index=index)
    shouted = search_policies("OPENING HOURS???", index=index)
    assert plain["results"][0]["source"] == shouted["results"][0]["source"]
    assert plain["results"][0]["heading"] == shouted["results"][0]["heading"]


def test_tool_clamps_weird_top_k(index):
    assert len(search_policies("bike", index=index, top_k=0)["results"]) == 1
    assert len(search_policies("bike", index=index, top_k="nonsense")["results"]) <= 3


def test_index_rejects_empty_corpus():
    with pytest.raises(ValueError):
        PolicyIndex([])


def test_missing_directory_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError):
        PolicyIndex.from_directory(tmp_path / "does-not-exist")
