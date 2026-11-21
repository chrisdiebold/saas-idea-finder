from ideafinder.db.models import Item
from ideafinder.pipeline.prefilter import decide


def item(body: str, kind: str = "comment", title: str | None = None, **extra) -> Item:
    return Item(kind=kind, body=body, title=title, extra=extra)


def test_agreement_comments_become_votes():
    for text in [
        "+1",
        "+ 1",
        "Upvote",
        "bump",
        "Any update on this?",
        "Please add this feature!",
        "+1 please",
        "We need this too",
    ]:
        assert decide(item(text)).outcome == "vote", text


def test_agreement_with_substance_is_kept():
    text = (
        "+1, we export 300 issues by hand every Friday because the CSV export drops custom fields."
    )
    assert decide(item(text)).outcome == "keep"


def test_short_and_junk_items_are_dropped():
    assert decide(item("Thanks!")).reason == "too_short"
    assert decide(item("https://getsupport.atlassian.com/browse/JST-955717")).reason == "too_short"
    assert (
        decide(item("439 322 1001 ##### 5555 77777 0000 12345 999 8888888 22")).reason
        == "not_prose"
    )


def test_launches_go_to_market():
    assert decide(item("", kind="post", title="Show HN: X", show_hn=True)).reason == "show_hn"
    launch = item(
        "", kind="post", title="Files.md – open-source alternative to Obsidian", link=None
    )
    assert decide(launch).reason == "launch_title"
    question = item(
        "Been trying to switch away from it.",
        kind="post",
        title="Ask HN: Alternatives to Claude (Code)?",
        link=None,
    )
    assert decide(question).outcome == "keep"


def test_hiring_thread_comments_are_dropped():
    job = item(
        "Acme | Remote | Full-time | We build invoicing software for plumbers and more.",
        story_title="Ask HN: Who is hiring? (November 2026)",
    )
    assert decide(job).reason == "job_post"
