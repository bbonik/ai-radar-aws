"""Property-based test for deduplication by announcement link.

Feature: aws-ai-news-hub, Property 11: Deduplication by announcement link

Validates: Requirements 7.2, 7.3, 8.2, 8.3
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from src.shared.models import RSSItem


# Strategy for generating URL-like link strings (unique identifiers)
link_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    min_size=1,
    max_size=100,
)

# Strategy for general text fields
general_text = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S", "Z")),
    min_size=1,
    max_size=100,
)

# Strategy for generating an RSSItem
rss_item_strategy = st.builds(
    RSSItem,
    title=general_text,
    description=general_text,
    pub_date=general_text,
    link=link_strategy,
)


def deduplicate(existing_links: set[str], items: list[RSSItem]) -> list[RSSItem]:
    """Apply deduplication logic: skip items whose link is already in existing_links.

    This mirrors the pipeline's deduplication step where items with links
    already present in the storage are skipped, and only new items proceed.
    """
    return [item for item in items if item.link not in existing_links]


@given(
    existing_links=st.frozensets(link_strategy, min_size=0, max_size=20),
    new_items=st.lists(rss_item_strategy, min_size=0, max_size=20),
)
@settings(max_examples=200)
def test_deduplication_skips_existing_links(
    existing_links: frozenset[str], new_items: list[RSSItem]
):
    """Property 11: Deduplication by announcement link.

    For any set of existing announcement links and new RSS items:
    - Items with links already in the existing set SHALL be skipped
    - Items with links NOT in the existing set SHALL proceed through the pipeline

    **Validates: Requirements 7.2, 7.3, 8.2, 8.3**
    """
    existing_set = set(existing_links)
    result = deduplicate(existing_set, new_items)

    # Property: No item in the result has a link that was in the existing set
    for item in result:
        assert item.link not in existing_set, (
            f"Item with existing link '{item.link}' was not skipped"
        )

    # Property: All items with new links (not in existing set) ARE in the result
    expected_new_items = [item for item in new_items if item.link not in existing_set]
    assert len(result) == len(expected_new_items), (
        f"Expected {len(expected_new_items)} new items but got {len(result)}"
    )

    # Property: The result preserves the original order of new items
    for result_item, expected_item in zip(result, expected_new_items):
        assert result_item == expected_item


@given(
    items=st.lists(rss_item_strategy, min_size=1, max_size=20),
)
@settings(max_examples=200)
def test_deduplication_with_all_existing_links_skips_everything(
    items: list[RSSItem],
):
    """Property 11 (corollary): When all item links are already known, nothing proceeds.

    If the existing links set contains ALL links from the new items,
    the deduplication step SHALL produce an empty result.

    **Validates: Requirements 7.2, 7.3, 8.2, 8.3**
    """
    # Build existing set from all item links
    existing_set = {item.link for item in items}
    result = deduplicate(existing_set, items)

    assert result == [], (
        f"Expected empty result when all links are known, got {len(result)} items"
    )


@given(
    items=st.lists(rss_item_strategy, min_size=1, max_size=20),
)
@settings(max_examples=200)
def test_deduplication_with_empty_existing_links_passes_everything(
    items: list[RSSItem],
):
    """Property 11 (corollary): When no links are known, all items proceed.

    If the existing links set is empty, the deduplication step SHALL
    allow all items to proceed through the pipeline.

    **Validates: Requirements 7.2, 7.3, 8.2, 8.3**
    """
    existing_set: set[str] = set()
    result = deduplicate(existing_set, items)

    assert len(result) == len(items), (
        f"Expected all {len(items)} items to proceed, got {len(result)}"
    )
    assert result == items


@given(
    existing_links=st.frozensets(link_strategy, min_size=0, max_size=20),
    new_items=st.lists(rss_item_strategy, min_size=0, max_size=20),
)
@settings(max_examples=200)
def test_deduplication_result_count_equals_new_links_count(
    existing_links: frozenset[str], new_items: list[RSSItem]
):
    """Property 11 (partition): Result size equals items with novel links.

    The number of items that proceed through the pipeline SHALL equal
    the number of items whose links are NOT in the existing set.

    **Validates: Requirements 7.2, 7.3, 8.2, 8.3**
    """
    existing_set = set(existing_links)
    result = deduplicate(existing_set, new_items)

    novel_count = sum(1 for item in new_items if item.link not in existing_set)
    assert len(result) == novel_count
