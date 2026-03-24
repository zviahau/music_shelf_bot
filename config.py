"""
Project configuration for the music autopilot.

Keep values here so `autopilot.py` stays focused on logic.
"""

from __future__ import annotations

from pathlib import Path


# Folder containing your audio files.
# `autopilot.py` expects this folder to exist (relative to this repo root).
MUSIC_INPUT_DIR: Path = Path(__file__).resolve().parent / "music_input"


# The task requirement: scan for supported audio files.
AUDIO_EXTENSIONS: tuple[str, ...] = (".mp3", ".m4a")

# Backward-compat alias (in case older code imports this symbol).
MP3_EXTENSIONS: tuple[str, ...] = (".mp3",)

