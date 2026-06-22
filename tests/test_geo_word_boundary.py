"""Tests for Task 13 (Part B):
Geo keyword matching uses word boundaries (e.g. "paris" must NOT match
inside "comparison"), preventing false-positive geography detection.
"""
import sys

sys.path.insert(0, ".")
from src.pipeline.importance_classifier import geo_keyword_in_text


class TestGeoWordBoundary:
    def test_paris_not_matched_inside_comparison(self):
        text = "running a controlled comparison between agent versions"
        assert geo_keyword_in_text("paris", text) is False

    def test_paris_matched_as_word(self):
        text = "now available in paris region"
        assert geo_keyword_in_text("paris", text) is True

    def test_region_code_matched(self):
        text = "available in ap-northeast-1 today"
        assert geo_keyword_in_text("ap-northeast-1", text) is True

    def test_multiword_phrase_matched(self):
        text = "expanding to the asia pacific area"
        assert geo_keyword_in_text("asia pacific", text) is True
