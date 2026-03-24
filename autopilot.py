from __future__ import annotations

import contextlib
import asyncio
import shutil
import tempfile
import time
import mimetypes
import os
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import requests

from dotenv import load_dotenv
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError
from tinytag import TinyTag

from aiogram import Bot, Dispatcher, F
from aiogram.types import FSInputFile, InputMediaAudio, InputMediaPhoto, Message
from openai import OpenAI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import AUDIO_EXTENSIONS, MUSIC_INPUT_DIR

from aiohttp import web
import threading

# Функция для запуска фейкового веб-сервера
async def hello(request):
    return web.Response(text="I am alive!")

def run_health_check():
    app = web.Application()
    app.add_routes([web.get('/', hello)])
    # Запускаем на порту 8000, который так хочет Koyeb
    web.run_app(app, port=8000)

# Запускаем сервер в отдельном потоке, чтобы он не мешал боту
threading.Thread(target=run_health_check, daemon=True).start()


def _image_ext_from_content_type(content_type: "str | None") -> str:
    if not content_type:
        return ".jpg"
    mime = content_type.split(";", 1)[0].strip().lower()
    ext = mimetypes.guess_extension(mime)
    return ext if ext else ".jpg"


def get_track_cover(artist: str, title: str) -> Optional[Path]:
    """
    Find the album cover for the given track (artist + title) using iTunes Search API.

    - Calls iTunes Search API via `requests`
    - Downloads the cover artwork to a temp file
    - Returns `None` when no cover is found (so the bot can skip sending a "white" image)
    """

    def _norm(s: str) -> str:
        # Normalize for comparisons: lowercase, trim, collapse spaces.
        return " ".join((s or "").strip().lower().split())

    def _pick_artwork_url(result: dict) -> str | None:
        # Prefer a larger image when possible.
        artwork = (
            result.get("artworkUrl100")
            or result.get("artworkUrl60")
            or result.get("artworkUrl600")
            or result.get("artworkUrl512")
        )
        if not artwork:
            return None
        # iTunes often uses 100x100/60x60 suffix. Upgrade to ~600x600 when available.
        if "100x100" in artwork:
            return artwork.replace("100x100", "600x600")
        if "60x60" in artwork:
            return artwork.replace("60x60", "600x600")
        return artwork

    def _search_music_tracks(term: str) -> list[dict]:
        url = (
            "https://itunes.apple.com/search"
            f"?term={quote_plus(term)}&entity=musicTrack&media=music&limit=10"
        )
        resp = requests.get(url, timeout=10, headers={"Accept": "application/json"})
        resp.raise_for_status()
        payload = resp.json()
        return payload.get("results") or []

    artist = (artist or "").strip()
    title = (title or "").strip()
    artist_norm = _norm(artist)
    title_norm = _norm(title)

    def _find_exact(entities: list[dict]) -> dict | None:
        for r in entities:
            if _norm(r.get("trackName", "")) == title_norm and _norm(r.get("artistName", "")) == artist_norm:
                return r
        return None

    def _find_by_title(entities: list[dict]) -> dict | None:
        # 1) Prefer exact trackName match; if multiple, prefer matching artistName.
        # 2) If iTunes returns no exact trackName match, still fall back to the first result.
        exact_match: dict | None = None
        for r in entities:
            if _norm(r.get("trackName", "")) != title_norm:
                continue
            if _norm(r.get("artistName", "")) == artist_norm:
                return r
            exact_match = exact_match or r
        return exact_match or (entities[0] if entities else None)

    def _find_by_artist(entities: list[dict]) -> dict | None:
        # Prefer exact artistName match; if not found, use the first result.
        for r in entities:
            if _norm(r.get("artistName", "")) == artist_norm:
                return r
        return entities[0] if entities else None

    try:
        # 1) Exact match: "artist - title".
        if artist_norm and title_norm:
            results = _search_music_tracks(f"{artist} {title}")
            exact = _find_exact(results)
            if exact:
                artwork_url = _pick_artwork_url(exact)
                if artwork_url:
                    cover_resp = requests.get(
                        artwork_url,
                        stream=True,
                        timeout=20,
                        headers={"User-Agent": "shelf_autopilot/1.0"},
                    )
                    cover_resp.raise_for_status()
                    ext = _image_ext_from_content_type(cover_resp.headers.get("content-type"))
                    with tempfile.NamedTemporaryFile(prefix="track_cover_", suffix=ext, delete=False) as tmp:
                        for chunk in cover_resp.iter_content(chunk_size=8192):
                            if chunk:
                                tmp.write(chunk)
                        return Path(tmp.name)

        # 2) Fallback: only title.
        if title_norm:
            results = _search_music_tracks(title)
            chosen = _find_by_title(results)
            if chosen:
                artwork_url = _pick_artwork_url(chosen)
                if artwork_url:
                    cover_resp = requests.get(
                        artwork_url,
                        stream=True,
                        timeout=20,
                        headers={"User-Agent": "shelf_autopilot/1.0"},
                    )
                    cover_resp.raise_for_status()
                    ext = _image_ext_from_content_type(cover_resp.headers.get("content-type"))
                    with tempfile.NamedTemporaryFile(prefix="track_cover_", suffix=ext, delete=False) as tmp:
                        for chunk in cover_resp.iter_content(chunk_size=8192):
                            if chunk:
                                tmp.write(chunk)
                        return Path(tmp.name)

        # 3) Fallback: only artist.
        if artist_norm:
            results = _search_music_tracks(artist)
            chosen = _find_by_artist(results)
            if chosen:
                artwork_url = _pick_artwork_url(chosen)
                if artwork_url:
                    cover_resp = requests.get(
                        artwork_url,
                        stream=True,
                        timeout=20,
                        headers={"User-Agent": "shelf_autopilot/1.0"},
                    )
                    cover_resp.raise_for_status()
                    ext = _image_ext_from_content_type(cover_resp.headers.get("content-type"))
                    with tempfile.NamedTemporaryFile(prefix="track_cover_", suffix=ext, delete=False) as tmp:
                        for chunk in cover_resp.iter_content(chunk_size=8192):
                            if chunk:
                                tmp.write(chunk)
                        return Path(tmp.name)

        return None
    except Exception:
        return None


def _guess_extension_from_mime(mime_type: str | None) -> Optional[str]:
    """
    Infer extension from Telegram mime type.

    We only map what we actually support in `config.AUDIO_EXTENSIONS`.
    """
    if not mime_type:
        return None
    mt = mime_type.lower().split(";", 1)[0].strip()

    if mt in {"audio/mpeg", "audio/mp3"}:
        return ".mp3"
    if mt in {"audio/mp4", "audio/x-m4a", "video/mp4"}:
        # Telegram often uses audio/mp4 for m4a files.
        return ".m4a"
    return None


def _safe_filename(filename: str) -> str:
    # Drop any directory components just in case.
    return Path(filename).name


def _resolve_destination_path(file_name: str, mime_type: str | None) -> Path:
    file_name = _safe_filename(file_name or "").strip()
    guessed_ext = _guess_extension_from_mime(mime_type)

    ext = Path(file_name).suffix.lower()
    if ext not in AUDIO_EXTENSIONS and guessed_ext in AUDIO_EXTENSIONS:
        # Normalize name to chosen extension.
        file_name = f"{Path(file_name).stem}{guessed_ext}"
        ext = guessed_ext

    if ext not in AUDIO_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {file_name} ({mime_type})")

    MUSIC_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = MUSIC_INPUT_DIR / file_name
    if dest.exists():
        dest = MUSIC_INPUT_DIR / f"{dest.stem}_{int(time.time())}{dest.suffix}"
    return dest


async def save_incoming_audio(message: Message, my_user_id: int) -> bool:
    """
    Save uploaded audio file into `music_input/` so the publisher loop can pick it up.
    """
    if not message.from_user or message.from_user.id != my_user_id:
        # Only accept uploads from your own user.
        return False

    bot = message.bot

    file_id: str | None = None
    file_name: str | None = None
    mime_type: str | None = None

    if message.document:
        file_id = message.document.file_id
        file_name = message.document.file_name
        mime_type = message.document.mime_type
    elif message.audio:
        file_id = message.audio.file_id
        file_name = message.audio.file_name
        mime_type = message.audio.mime_type

    if not file_id:
        await message.answer("Не получилось определить файл.")
        return False

    try:
        dest_path = _resolve_destination_path(
            file_name or f"upload_{file_id}.mp3",
            mime_type,
        )
    except ValueError:
        await message.answer("Поддерживаются только `.mp3` и `.m4a` файлы.")
        return False

    try:
        tg_file = await bot.get_file(file_id)
        await bot.download_file(tg_file.file_path, destination=dest_path)
    except Exception as e:
        await message.answer(f"Ошибка при скачивании файла: {e}")
        return False

    return True


def iter_audio_files(folder: Path) -> list[Path]:
    """Return all supported audio files (case-insensitive) under `folder`."""
    if not folder.exists():
        return []

    result: list[Path] = []
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS:
            result.append(p)
    return sorted(result)


def read_artist_and_title(audio_path: Path) -> tuple[str, str]:
    """
    Read Artist and Title from audio file metadata.

    Primary path uses `tinytag` so `.m4a` tags are handled correctly.
    """
    try:
        tag = TinyTag.get(str(audio_path))
        artist = (tag.artist or "").strip() or "Unknown Artist"
        title = (tag.title or "").strip() or (audio_path.stem or "Unknown Title")
        return artist, title
    except Exception:
        # Fallback for MP3 ID3 tags (in case `tinytag` fails for some reason).
        if audio_path.suffix.lower() != ".mp3":
            return "Unknown Artist", audio_path.stem or "Unknown Title"
        try:
            tags = EasyID3(str(audio_path))
        except ID3NoHeaderError:
            return "Unknown Artist", audio_path.stem or "Unknown Title"

        artist_list = tags.get("artist") or []
        title_list = tags.get("title") or []

        artist = artist_list[0] if artist_list else "Unknown Artist"
        title = title_list[0] if title_list else audio_path.stem or "Unknown Title"
        return artist, title


def generate_description(artist: str, title: str) -> str:
    """
    Generate a short Russian caption in the spirit of "Music Shelf".

    Note: the "style" of the neural network output is controlled by the prompt
    in `SYSTEM_STYLE_PROMPT` below.
    """

    load_dotenv()

    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not openai_api_key:
        raise RuntimeError("Missing OPENAI_API_KEY in environment (.env).")

    # Adjust writing style for the neural network here.
    # Keep it short, but specify tone, length, and what to include.
    SYSTEM_STYLE_PROMPT = (
        "Ты пишешь короткие, стильные посты для телеграм-рубрики 'Music Shelf'. "
        "Тон: чуть поэтичный и дружелюбный, как будто мы выбираем трек с полки "
        "и рекомендуем его на вечер. Пиши по-русски. "
        "Соблюдай краткость: максимум ~700 символов. "
        "В конце добавь 2-4 релевантных хэштега (с #). "
        "Добавь пару уместных эмодзи (2-3). Не используй ссылки и не обещай невозможного."
    )

    client = OpenAI(api_key=openai_api_key)

    user_prompt = (
        f"Артист: {artist}\n"
        f"Название: {title}\n\n"
        "Сгенерируй пост для Music Shelf: 1 абзац + хэштеги. "
        "Сделай так, чтобы было ощущение 'настроения трека', но без выдуманных фактов: "
        "только мягко опиши впечатление."
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_STYLE_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.9,
        )

        text = (resp.choices[0].message.content or "").strip()
        if text:
            return text
    except Exception as e:
        # Keep the bot functional if OpenAI is down, rate-limited or quota-exceeded.
        # (In particular, your current error is 429: insufficient_quota.)
        # We intentionally keep a friendly post style via a deterministic template.
        _ = e

    # Fallback: deterministic template with the same vibe.
    return f"На полке сегодня: {artist} — {title} 🎧✨\nТёплый вайб, чтобы включить настроение. #MusicShelf #music"


async def send_post(audio_path: Path, caption: str, artist: str, title: str) -> None:
    """Send a "cover + caption + audio" post to the configured Telegram channel."""

    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    channel_id = os.getenv("CHANNEL_ID", "").strip()
    if not bot_token:
        raise RuntimeError("Missing BOT_TOKEN in environment (.env).")
    if not channel_id:
        raise RuntimeError("Missing CHANNEL_ID in environment (.env).")

    bot = Bot(token=bot_token)
    cover_path: Optional[Path] = None
    try:
        cover_path = get_track_cover(artist, title)

        if cover_path and cover_path.exists():
            # Best experience: album-like message with cover (caption) + audio below it.
            await bot.send_media_group(
                chat_id=int(channel_id),
                media=[
                    InputMediaPhoto(
                        media=FSInputFile(str(cover_path)),
                        caption=caption,
                        # Ensure caption is below the photo in Telegram UI.
                        show_caption_above_media=False,
                    ),
                    # Telegram supports streaming/playing `.m4a` as audio when sent via `send_audio`/`InputMediaAudio`.
                    InputMediaAudio(media=FSInputFile(str(audio_path))),
                ],
            )
        else:
            # No cover: send post without photo to avoid "white placeholder" images.
            await bot.send_audio(
                chat_id=int(channel_id),
                audio=FSInputFile(str(audio_path)),
                caption=caption,
            )
    except Exception:
        # Fallback: if media groups fail (Telegram restrictions, formatting, etc.),
        # send photo first, then audio without disrupting the flow.
        if cover_path and cover_path.exists():
            await bot.send_photo(
                chat_id=int(channel_id),
                photo=FSInputFile(str(cover_path)),
                caption=caption,
            )
            await bot.send_audio(chat_id=int(channel_id), audio=FSInputFile(str(audio_path)))
        else:
            await bot.send_audio(
                chat_id=int(channel_id),
                audio=FSInputFile(str(audio_path)),
                caption=caption,
            )
    finally:
        if cover_path and cover_path.exists():
            cover_path.unlink(missing_ok=True)
        await bot.session.close()


async def main_async() -> None:
    load_dotenv()
    MUSIC_INPUT_DIR.mkdir(parents=True, exist_ok=True)

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("Missing BOT_TOKEN in environment (.env).")

    user_id_raw = os.getenv("USER_ID", "").strip()
    if not user_id_raw:
        raise RuntimeError("Missing USER_ID in environment (.env).")
    my_user_id = int(user_id_raw)

    bot = Bot(token=bot_token)
    dp = Dispatcher()

    @dp.message(F.document)
    async def _on_document(message: Message) -> None:
        if await save_incoming_audio(message, my_user_id):
            await message.answer("Трек в очереди!")

    @dp.message(F.audio)
    async def _on_audio(message: Message) -> None:
        if await save_incoming_audio(message, my_user_id):
            await message.answer("Трек в очереди!")

    # Scheduler: publish strictly on schedule (Mon/Wed/Fri at 17:00 Moscow time).
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    trigger = CronTrigger(
        day_of_week="mon,wed,fri",
        hour=17,
        minute=0,
        timezone="Europe/Moscow",
    )

    async def _job() -> None:
        # Keep scheduler alive even if publishing fails.
        try:
            await publish_next_track()
        except Exception as e:
            print(f"publish_next_track failed: {e}")

    scheduler.add_job(
        _job,
        trigger=trigger,
        name="publish_next_track",
        max_instances=1,
    )
    scheduler.start()

    try:
        # Infinite wait for updates (polling).
        await dp.start_polling(bot)
    finally:
        with contextlib.suppress(Exception):
            scheduler.shutdown(wait=False)


async def publish_next_track() -> None:
    """
    Publish exactly one next track from `music_input/`.

    This function is intended to be called by APScheduler.
    """
    audio_files = iter_audio_files(MUSIC_INPUT_DIR)
    if not audio_files:
        print(f"No supported audio files found in: {MUSIC_INPUT_DIR} (extensions: {AUDIO_EXTENSIONS})")
        return

    # Requirement: process only the first file.
    audio_path = audio_files[0]
    if audio_path.suffix.lower() not in AUDIO_EXTENSIONS:
        # Defensive: ignore unexpected extensions even if they slipped into `audio_files`.
        return

    artist, title = read_artist_and_title(audio_path)
    # Python 3.8 compatibility: run the blocking OpenAI call in a thread pool.
    loop = asyncio.get_running_loop()
    caption = await loop.run_in_executor(None, generate_description, artist, title)

    await send_post(audio_path, caption, artist, title)

    posted_dir = MUSIC_INPUT_DIR.parent / "posted"
    posted_dir.mkdir(parents=True, exist_ok=True)

    dest_path = posted_dir / audio_path.name
    if dest_path.exists():
        dest_path = posted_dir / f"{audio_path.stem}_{int(time.time())}{audio_path.suffix}"

    shutil.move(str(audio_path), str(dest_path))

    print(f"Published: {artist} - {title}")
    print(f"Moved to: {dest_path}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

