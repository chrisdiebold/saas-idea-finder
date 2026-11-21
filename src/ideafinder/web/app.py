"""The localhost web app. For now: a status page. Review screens arrive in M5."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ideafinder.settings import get_settings
from ideafinder.status import get_status

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

app = FastAPI(title="SaaS Idea Finder", docs_url=None, redoc_url=None)


# Plain `def` routes: status checks use blocking database calls, so FastAPI runs them in a thread.
@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "status.html", {"status": get_status(get_settings())}
    )


@app.get("/healthz")
def healthz() -> dict:
    status = get_status(get_settings())
    return {
        "ok": not status.problems,
        "problems": status.problems,
        "database": {
            "reachable": status.database.reachable,
            "up_to_date": status.database.up_to_date,
            "revision": status.database.current_revision,
        },
        "queues": {q.queue: q.alive for q in status.queues},
    }
