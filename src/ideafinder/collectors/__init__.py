"""Collectors fetch Items from Sources. See PLAN.md §1 and §3 (Collect)."""

from ideafinder.collectors.base import Collector
from ideafinder.collectors.bluesky import BlueskyCollector
from ideafinder.collectors.feature_boards import FeatureBoardsCollector
from ideafinder.collectors.github import GitHubCollector
from ideafinder.collectors.hackernews import HackerNewsCollector
from ideafinder.collectors.jira import JiraCollector
from ideafinder.collectors.mastodon import MastodonCollector
from ideafinder.collectors.producthunt import ProductHuntCollector
from ideafinder.collectors.shopify_apps import ShopifyAppsCollector
from ideafinder.collectors.stackexchange import StackExchangeCollector
from ideafinder.collectors.wordpress import WordPressCollector
from ideafinder.collectors.zapier import ZapierCollector

COLLECTORS: dict[str, type[Collector]] = {
    c.source_key: c
    for c in (
        HackerNewsCollector,
        JiraCollector,
        WordPressCollector,
        StackExchangeCollector,
        GitHubCollector,
        ShopifyAppsCollector,
        FeatureBoardsCollector,
        ZapierCollector,
        ProductHuntCollector,
        MastodonCollector,
        BlueskyCollector,
    )
}
