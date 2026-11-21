"""Collectors fetch Items from Sources. See PLAN.md §1 and §3 (Collect)."""

from ideafinder.collectors.base import Collector
from ideafinder.collectors.hackernews import HackerNewsCollector
from ideafinder.collectors.jira import JiraCollector
from ideafinder.collectors.stackexchange import StackExchangeCollector
from ideafinder.collectors.wordpress import WordPressCollector

COLLECTORS: dict[str, type[Collector]] = {
    c.source_key: c
    for c in (HackerNewsCollector, JiraCollector, WordPressCollector, StackExchangeCollector)
}
