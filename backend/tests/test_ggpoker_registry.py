"""Registry-level tests for the GGPoker parser.

This fork only supports GGPoker, so these cover the pieces that the
per-hand parser tests in test_parser.py don't: site detection/routing,
hand splitting and hand-id extraction.
"""

from pathlib import Path

from app.parsers import detect_parser, PARSERS, PARSER_BY_SITE_ID
from app.parsers.ggpoker import (
    SITE_CODE,
    SITE_ID,
    extract_hand_id,
    split_hands,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ggpoker"


def test_only_ggpoker_is_registered():
    assert [p.SITE_CODE for p in PARSERS] == ["GG"]


def test_detect_routes_to_ggpoker():
    sample = (FIXTURES / "sample.txt").read_text()[:500]
    parser = detect_parser(sample)
    assert parser is not None
    assert parser.SITE_CODE == "GG"


def test_registry_lookup_by_site_id():
    assert PARSER_BY_SITE_ID[SITE_ID].SITE_CODE == SITE_CODE


def test_hand_id_extracted():
    text = (FIXTURES / "sample.txt").read_text()
    assert extract_hand_id(text)


def test_split_hands_yields_parseable_hands():
    text = (FIXTURES / "sample.txt").read_text()
    hands = [h for h in split_hands(text) if h.strip()]
    assert len(hands) >= 1
    assert all(h.lstrip().startswith("Poker Hand #") for h in hands)


def test_unrelated_format_is_not_detected():
    """A non-GGPoker header must not be claimed by the GGPoker parser."""
    assert detect_parser("PokerStars Hand #123456:  Hold'em No Limit ($1/$2)") is None
