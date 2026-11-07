"""What every service shares: app factory, health endpoints, API key check, background loops."""
import asyncio
import hmac
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header, HTTPException, status
from sqlalchemy import text

from . import events
from .config import get_settings
from .db import session_scope

log = logging.getLogger(__name__)


def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    """Guards write endpoints. With no key configured, writes stay closed."""
    expected = get_settings().api_key
    if not expected:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Set IFIC_API_KEY to enable write endpoints.")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or wrong X-API-Key header.")


def create_app(title: str, router: APIRouter, workers: list[Callable[[], Awaitable[None]]]) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        tasks = [asyncio.create_task(w()) for w in workers] if get_settings().workers_enabled else []
        yield
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title=title, version="0.1.0", lifespan=lifespan)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": title}

    @app.get("/ready", tags=["ops"])
    def ready() -> dict[str, str]:
        with session_scope() as s:
            s.execute(text("SELECT 1"))
        return {"status": "ready"}

    app.include_router(router)
    return app


async def consume(topic: str, handler: Callable[[dict[str, Any]], None]) -> None:
    """Runs one topic's events through a handler, off the event loop, until cancelled."""
    poll = get_settings().worker_poll_s
    while True:
        try:
            event = await asyncio.to_thread(events.claim, topic)
            if event is None:
                await asyncio.sleep(poll)
                continue
            try:
                await asyncio.to_thread(handler, event.payload)
                await asyncio.to_thread(events.complete, event.id)
            except Exception as exc:
                log.exception("handler failed for event %s (%s)", event.id, topic)
                await asyncio.to_thread(events.fail, event.id, f"{type(exc).__name__}: {exc}")
        except asyncio.CancelledError:
            raise
        except Exception:  # e.g. database briefly unavailable: keep the worker alive
            log.exception("consumer loop error on %s", topic)
            await asyncio.sleep(poll)


async def every(interval_s: float, job: Callable[[], Any]) -> None:
    """Runs a blocking job on a fixed interval, starting immediately."""
    while True:
        try:
            await asyncio.to_thread(job)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("periodic job %s failed", getattr(job, "__name__", job))
        await asyncio.sleep(interval_s)
