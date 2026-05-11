"""Property-based test for CSV serialization round-trip consistency.

Feature: aws-ai-news-hub, Property 10: CSV serialization round-trip

Validates: Requirements 7.1
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from src.shared.models import ProcessedAnnouncement, Report


# Strategy for generating printable text without pipe characters for blogpost links
printable_no_pipe = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S", "Z"),
        blacklist_characters="|",
    ),
    min_size=1,
    max_size=100,
)

# Strategy for general text fields (printable, reasonable length)
general_text = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S", "Z")),
    min_size=1,
    max_size=200,
)

# Strategy for generating a Report dataclass
report_strategy = st.builds(
    Report,
    whats_new=general_text,
    how_it_works=general_text,
    why_important=general_text,
    how_different=general_text,
    when_to_prefer=general_text,
    availability=general_text,
)

# Strategy for mermaid_graph: either None or a non-empty string
mermaid_graph_strategy = st.one_of(
    st.none(),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "S", "Z")),
        min_size=1,
        max_size=200,
    ),
)

# Strategy for importance_score: finite floats with limited precision for clean round-trip
importance_score_strategy = st.floats(
    min_value=-1e6,
    max_value=1e6,
    allow_nan=False,
    allow_infinity=False,
).map(lambda x: round(x, 6))

# Strategy for blogpost_links: list of strings without pipe characters
blogpost_links_strategy = st.lists(printable_no_pipe, min_size=0, max_size=5)

# Full strategy for ProcessedAnnouncement
processed_announcement_strategy = st.builds(
    ProcessedAnnouncement,
    title=general_text,
    description=general_text,
    pub_date=general_text,
    link=general_text,
    aws_service=general_text,
    importance_level=st.sampled_from([1, 2, 3, 4, 5]),
    importance_score=importance_score_strategy,
    report=report_strategy,
    mermaid_graph=mermaid_graph_strategy,
    blogpost_links=blogpost_links_strategy,
    first_detected=general_text,
)


@given(announcement=processed_announcement_strategy)
@settings(max_examples=100)
def test_csv_round_trip_consistency(announcement: ProcessedAnnouncement):
    """Property 10: CSV serialization round-trip.

    For any ProcessedAnnouncement, serializing to a CSV row dict via to_csv_row()
    and then deserializing back via from_csv_row() SHALL produce an equivalent object.

    Feature: aws-ai-news-hub, Property 10: CSV serialization round-trip
    Validates: Requirements 7.1
    """
    # Serialize to CSV row
    csv_row = announcement.to_csv_row()

    # Deserialize back
    restored = ProcessedAnnouncement.from_csv_row(csv_row)

    # Verify equivalence of all fields
    assert restored.title == announcement.title
    assert restored.description == announcement.description
    assert restored.pub_date == announcement.pub_date
    assert restored.link == announcement.link
    assert restored.aws_service == announcement.aws_service
    assert restored.importance_level == announcement.importance_level
    assert restored.importance_score == announcement.importance_score
    assert restored.report == announcement.report
    assert restored.mermaid_graph == announcement.mermaid_graph
    assert restored.blogpost_links == announcement.blogpost_links
    assert restored.first_detected == announcement.first_detected

    # Full object equality check
    assert restored == announcement
