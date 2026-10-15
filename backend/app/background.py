"""Background work started in the FastAPI lifespan: the analysis worker pool and
the watchlist poller (every WATCHLIST_INTERVAL_SECONDS, default 5 minutes)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.config import get_settings

log = logging.getLogger("ronntech.background")


@dataclass
class BackgroundHandle:
    tasks: list[asyncio.Task]


async def _watchlist_loop(interval: int) -> None:
    while True:
        try:
            from app.alerts.watcher import poll_all

            await asyncio.to_thread(poll_all)
        except ModuleNotFoundError:
            pass
        except Exception:  # noqa: BLE001 - keep polling
            log.exception("watchlist poll failed")
        await asyncio.sleep(interval)


async def start_background() -> BackgroundHandle:
    settings = get_settings()
    tasks = [asyncio.create_task(_watchlist_loop(int(settings.WATCHLIST_INTERVAL_SECONDS)))]
    return BackgroundHandle(tasks=tasks)


async def stop_background(handle: BackgroundHandle) -> None:
    for t in handle.tasks:
        t.cancel()
    for t in handle.tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    try:
        from app.engine.jobs import shutdown

        shutdown()
    except ModuleNotFoundError:
        pass
