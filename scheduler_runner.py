from __future__ import annotations

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from autopilot import publish_next_track


async def _job() -> None:
    # Keep scheduler alive even if publishing fails.
    try:
        await publish_next_track()
    except Exception as e:
        print(f"publish_next_track failed: {e}")


async def main_async() -> None:
    # Use Moscow time zone explicitly.
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

    trigger = CronTrigger(
        day_of_week="mon,wed,fri",
        hour=17,
        minute=0,
        timezone="Europe/Moscow",
    )

    scheduler.add_job(_job, trigger=trigger, name="publish_next_track")
    scheduler.start()

    # Run forever.
    while True:
        await asyncio.sleep(3600)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

