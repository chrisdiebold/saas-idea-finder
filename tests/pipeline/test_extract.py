from ideafinder.db.models import Item
from ideafinder.pipeline.extract import (
    MAX_ITEMS_PER_CHUNK,
    Chunk,
    chunk_thread,
    parse_output,
    quote_appears,
    render_chunk,
)


def item(i: int, body: str, kind: str = "comment", title: str | None = None) -> Item:
    return Item(id=i, kind=kind, body=body, title=title, author=f"user{i}", extra={})


def test_chunks_split_by_count_and_size():
    items = [item(i, "x" * 100) for i in range(MAX_ITEMS_PER_CHUNK + 3)]
    chunks = chunk_thread("jira_atlassian", "T", items)
    assert [len(c.items) for c in chunks] == [MAX_ITEMS_PER_CHUNK, 3]
    big = [item(i, "y" * 1900) for i in range(6)]
    assert len(chunk_thread("jira_atlassian", "T", big)) == 2  # 9,000-character budget


def test_render_numbers_items_and_gives_context():
    text = render_chunk(
        Chunk(
            "jira_atlassian",
            "Bulk edit due dates",
            [item(1, "We edit hundreds by hand.", kind="issue", title="Bulk edit")],
        )
    )
    assert "Atlassian's public suggestion tracker" in text
    assert "Thread: Bulk edit due dates" in text
    assert "[Item 1] (issue, by user1)" in text and "Title: Bulk edit" in text


def test_quote_check_ignores_case_whitespace_and_quote_marks():
    it = item(1, "We   copy every invoice\ninto QuickBooks by hand.")
    assert quote_appears('"we copy every invoice into quickbooks…"', it)
    assert not quote_appears("we never copy anything", it)
    assert not quote_appears("", it)


def test_parse_output_validates_against_the_chunk():
    chunk = Chunk("hackernews", None, [item(10, "I'd pay for a tool that does our invoicing.")])
    data = {
        "signals": [
            {
                "item": 1,
                "kind": "payment",
                "problem_statement": " Invoicing takes too long. ",
                "persona": "",
                "intensity": 9,
                "quote": "I'd pay for a tool",
                "workaround": None,
                "products": [" QuickBooks ", ""],
            },
            {
                "item": 2,
                "kind": "pain",
                "problem_statement": "x",
                "persona": "p",
                "intensity": 2,
                "quote": "",
                "workaround": None,
                "products": [],
            },  # no Item 2
            {
                "item": 1,
                "kind": "market",
                "problem_statement": "x",
                "persona": "p",
                "intensity": 2,
                "quote": "",
                "workaround": None,
                "products": [],
            },  # not an extraction kind
        ]
    }
    [signal] = parse_output(chunk, data)
    assert signal.item.id == 10 and signal.kind == "payment"
    assert signal.problem_statement == "Invoicing takes too long."
    assert signal.persona == "unknown" and signal.intensity == 5
    assert signal.quote_verified and signal.products == ["QuickBooks"]
