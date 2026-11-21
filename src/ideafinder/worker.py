"""The procrastinate job-queue app, its queues, and its tasks.

Two queues keep the work separable (ADR 0003):
- `collect`: collection and everything that doesn't need the local model. Can move to the Pi later.
- `model`: everything that uses the local model or embeddings. Always runs on the Mac.
"""

from procrastinate import App, PsycopgConnector
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from ideafinder.db.engine import get_sessionmaker
from ideafinder.db.models import WorkerHeartbeat
from ideafinder.settings import get_settings

COLLECT_QUEUE = "collect"
MODEL_QUEUE = "model"
QUEUES = (COLLECT_QUEUE, MODEL_QUEUE)

app = App(connector=PsycopgConnector(conninfo=get_settings().database_url))


async def record_heartbeat(queue: str) -> None:
    stmt = insert(WorkerHeartbeat).values(queue=queue)
    stmt = stmt.on_conflict_do_update(index_elements=["queue"], set_={"last_seen": func.now()})
    async with get_sessionmaker()() as session, session.begin():
        await session.execute(stmt)


# One heartbeat per queue, every minute, so the status page can tell whether each queue is served.
@app.periodic(cron="* * * * *", periodic_id="heartbeat-collect")
@app.task(queue=COLLECT_QUEUE, name="heartbeat_collect")
async def heartbeat_collect(timestamp: int) -> None:
    await record_heartbeat(COLLECT_QUEUE)


@app.periodic(cron="* * * * *", periodic_id="heartbeat-model")
@app.task(queue=MODEL_QUEUE, name="heartbeat_model")
async def heartbeat_model(timestamp: int) -> None:
    await record_heartbeat(MODEL_QUEUE)


WORKER_OPTIONS = {
    "delete_jobs": "successful",  # keep only failed jobs, so the jobs table doesn't grow forever
}
