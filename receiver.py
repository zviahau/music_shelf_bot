from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message
from dotenv import load_dotenv

from config import MUSIC_INPUT_DIR


USER_USERNAME = "aleksey_zv"
ALLOWED_M4A_SUFFIX = ".m4a"

router = Router()


def _is_m4a_message(message: Message) -> bool:
    """
    Check whether Telegram message contains an M4A audio file.

    We rely on `file_name` extension when available.
    """
    if message.audio:
        file_name = message.audio.file_name or ""
        return file_name.lower().endswith(ALLOWED_M4A_SUFFIX)

    if message.document:
        file_name = message.document.file_name or ""
        return file_name.lower().endswith(ALLOWED_M4A_SUFFIX)

    return False


@router.message(F.from_user.username == USER_USERNAME)
async def on_user_message(message: Message, bot: Bot) -> None:
    # Accept only actual audio messages that are `.m4a`.
    if not _is_m4a_message(message):
        return

    file_id = None
    raw_name = None

    if message.audio and message.audio.file_id:
        file_id = message.audio.file_id
        raw_name = message.audio.file_name
    elif message.document and message.document.file_id:
        file_id = message.document.file_id
        raw_name = message.document.file_name

    if not file_id:
        return

    MUSIC_INPUT_DIR.mkdir(parents=True, exist_ok=True)

    raw_name = raw_name or f"track_{int(time.time())}{ALLOWED_M4A_SUFFIX}"
    safe_name = Path(raw_name).name  # Prevent path traversal in case of weird file_name.
    dest_path = MUSIC_INPUT_DIR / safe_name

    # Avoid overwriting an existing file.
    if dest_path.exists():
        dest_path = MUSIC_INPUT_DIR / f"{dest_path.stem}_{int(time.time())}{dest_path.suffix}"

    tg_file = await bot.get_file(file_id)
    await bot.download_file(tg_file.file_path, destination=dest_path)

    await message.answer("Трек принят и поставлен в очередь!")


async def main_async() -> None:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("Missing BOT_TOKEN in environment (.env).")

    bot = Bot(token=bot_token)
    dp = Dispatcher()
    dp.include_router(router)

    await dp.start_polling(bot)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

