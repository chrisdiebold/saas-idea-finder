"""Fake Source APIs for tests, shaped like the real responses (checked November 2026)."""

from typing import Any

import httpx2

T0 = 1_795_000_000  # a fixed "now"-ish unix time used by the fakes

SPANISH = (
    "Hola a todos, necesito una herramienta para gestionar las facturas de mis clientes "
    "porque hacerlo a mano en una hoja de cálculo me lleva horas cada semana."
)


def hn_story(object_id: str, ts: int, tags: tuple[str, ...] = ("story",), **kw) -> dict[str, Any]:
    return {
        "objectID": object_id,
        "created_at_i": ts,
        "author": "alice",
        "points": 12,
        "title": "Is there a tool for turning site photos into quotes?",
        "story_text": "<p>We do this by hand &amp; it takes hours.</p>",
        "url": None,
        "num_comments": 3,
        "_tags": [*tags, f"story_{object_id}"],
        **kw,
    }


def hn_comment(object_id: str, ts: int, story_id: str) -> dict[str, Any]:
    return {
        "objectID": object_id,
        "created_at_i": ts,
        "author": "bob",
        "points": None,
        "comment_text": "I&#x27;d pay for this.<p>We currently use a spreadsheet.",
        "story_id": int(story_id),
        "parent_id": int(story_id),
        "story_title": "Is there a tool for turning site photos into quotes?",
        "_tags": ["comment", f"story_{story_id}"],
    }


def hn_page(hits: list[dict], nb_hits: int | None = None, page: int = 0, per_page: int = 100):
    total = len(hits) if nb_hits is None else nb_hits
    return {
        "hits": hits,
        "nbHits": total,
        "page": page,
        "nbPages": max(1, -(-min(total, 1000) // per_page)),
        "hitsPerPage": per_page,
    }


def jira_issue(key: str = "JRACLOUD-1", comments: int = 1) -> dict[str, Any]:
    return {
        "key": key,
        "fields": {
            "summary": "Allow bulk editing of due dates",
            "description": "Customers need to change due dates on many issues at once.",
            "votes": {"votes": 412},
            "created": "2019-03-01T10:00:00.000+0000",
            "updated": "2026-11-20T09:00:00.000+0000",
            "reporter": {"displayName": "Carol"},
            "status": {"name": "Gathering Interest"},
            "comment": {
                "total": comments,
                "comments": [
                    {
                        "id": str(1000 + i),
                        "author": {"displayName": f"user{i}"},
                        "created": "2026-11-19T08:00:00.000+0000",
                        "body": "We need this badly, our team edits hundreds of issues by hand.",
                    }
                    for i in range(comments)
                ],
            },
        },
    }


def wp_feed(entries: list[tuple[str, str, int | None, str]]) -> bytes:
    """entries: (guid slug, title, rating or None, pubDate)."""
    items = []
    for slug, title, rating, date in entries:
        rating_line = f"<p>Rating: {rating} stars</p>" if rating else ""
        body = (
            SPANISH
            if slug.startswith("es-")
            else ("The plugin keeps breaking our checkout and support never answers our tickets.")
        )
        items.append(f"""
        <item>
          <guid>https://wordpress.org/support/topic/{slug}/</guid>
          <title>[WooCommerce] {title}{f" ({rating} stars)" if rating else ""}</title>
          <link>https://wordpress.org/support/topic/{slug}/</link>
          <pubDate>{date}</pubDate>
          <dc:creator>dave</dc:creator>
          <description><![CDATA[<p>Replies: 2</p>{rating_line}<p>{body}</p>]]></description>
        </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
    <title>Reviews</title>{"".join(items)}</channel></rss>""".encode()


def se_question(qid: int, ts: int, site: str = "softwarerecs") -> dict[str, Any]:
    return {
        "question_id": qid,
        "creation_date": ts,
        "score": 4,
        "view_count": 250,
        "answer_count": 1,
        "is_answered": True,
        "tags": ["invoicing"],
        "title": "Tool to send &quot;invoice&quot; reminders automatically?",
        "body": "<p>Is there a tool that emails clients when an invoice is overdue?</p>",
        "link": f"https://{site}.stackexchange.com/questions/{qid}",
        "owner": {"display_name": "Erin T&#252;r"},
    }


def se_page(items: list[dict], has_more: bool = False, quota: int = 9000, backoff=None):
    page = {"items": items, "has_more": has_more, "quota_max": 10000, "quota_remaining": quota}
    if backoff:
        page["backoff"] = backoff
    return page


def all_sources_transport(fail_hosts: frozenset[str] = frozenset()) -> httpx2.MockTransport:
    """One fake for every Phase 1 Source, for end-to-end runner tests."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        host = request.url.host
        if host in fail_hosts:
            return httpx2.Response(500)
        params = request.url.params
        if host == "hn.algolia.com":
            if params.get("tags") == "show_hn":
                return httpx2.Response(
                    200, json=hn_page([hn_story("900", T0, ("story", "show_hn"))])
                )
            return httpx2.Response(
                200, json=hn_page([hn_story("100", T0), hn_comment("101", T0 + 60, "100")])
            )
        if host == "jira.atlassian.com":
            return httpx2.Response(200, json={"startAt": 0, "total": 1, "issues": [jira_issue()]})
        if host == "wordpress.org":
            if "/reviews/" in request.url.path:
                return httpx2.Response(
                    200,
                    content=wp_feed(
                        [
                            ("bad-review", "Broken again", 2, "Fri, 20 Nov 2026 10:00:00 +0000"),
                            ("great-review", "Love it", 5, "Fri, 20 Nov 2026 11:00:00 +0000"),
                        ]
                    ),
                )
            return httpx2.Response(
                200,
                content=wp_feed(
                    [
                        (
                            "checkout-bug",
                            "Checkout broken",
                            None,
                            "Fri, 20 Nov 2026 12:00:00 +0000",
                        ),
                        ("es-facturas", "Facturas", None, "Fri, 20 Nov 2026 13:00:00 +0000"),
                    ]
                ),
            )
        if host == "api.stackexchange.com":
            return httpx2.Response(200, json=se_page([se_question(42, T0)]))
        return httpx2.Response(404)

    return httpx2.MockTransport(handler)
