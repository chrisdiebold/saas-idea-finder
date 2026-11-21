"""The localhost web app: the review screens, settings, and a status page."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ideafinder.settings import get_settings
from ideafinder.status import get_status
from ideafinder.web.routes import briefings as briefing_routes
from ideafinder.web.routes import eval as eval_routes
from ideafinder.web.routes import ideas as idea_routes
from ideafinder.web.routes import settings as settings_routes
from ideafinder.web.routes import validation as validation_routes
from ideafinder.web.routes import week as week_routes

app = FastAPI(title="SaaS Idea Finder", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(idea_routes.router)
app.include_router(briefing_routes.router)
app.include_router(validation_routes.router)
app.include_router(week_routes.router)
app.include_router(settings_routes.router)
app.include_router(eval_routes.router)


# Plain `def` routes: status checks use blocking database calls, so FastAPI runs them in a thread.
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
