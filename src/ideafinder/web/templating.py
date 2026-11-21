"""The one Jinja environment every page uses, with the labels shared across screens."""

from pathlib import Path
from urllib.parse import urlencode

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

REASON_LABELS = {
    "not_interested": "Not interested",
    "too_competitive": "Too competitive",
    "low_willingness_to_pay": "Low willingness to pay",
    "too_big_to_build": "Too big to build",
    "dropped_after_research": "Dropped after research",
    "outside_fit": "Outside fit",
}
# Reasons you can pick when archiving from the Inbox (the others have their own situations).
INBOX_REASONS = ("not_interested", "too_competitive", "low_willingness_to_pay", "too_big_to_build")

COMPONENT_LABELS = {
    "volume": "Volume",
    "spread": "Spread",
    "intensity": "Intensity",
    "payment": "Payment",
    "momentum": "Momentum",
    "competitive_gap": "Competitive gap",
    "fit": "Fit",
    "solo_buildable": "Solo-buildable",
}

STATE_LABELS = {
    "incubating": "Incubating",
    "inbox": "Inbox",
    "working": "Working List",
    "archived": "Archive",
}

ACTION_LABELS = {
    "published": "Reached the Inbox",
    "to_working": "Moved to the Working List",
    "to_archive": "Archived",
    "restored": "Restored to the Inbox",
    "resurfaced": "Resurfaced",
    "auto_outside_fit": "Archived automatically: outside fit",
    "merged": "Another Idea merged in",
    "split": "Signals split off",
}


def with_message(path: str, message: str | None = None, error: str | None = None) -> str:
    """A redirect target that shows a one-line message on the next page."""
    params = {k: v for k, v in (("msg", message), ("error", error)) if v}
    return f"{path}{'&' if '?' in path else '?'}{urlencode(params)}" if params else path


templates.env.globals.update(
    REASON_LABELS=REASON_LABELS,
    INBOX_REASONS=INBOX_REASONS,
    COMPONENT_LABELS=COMPONENT_LABELS,
    STATE_LABELS=STATE_LABELS,
    ACTION_LABELS=ACTION_LABELS,
)
