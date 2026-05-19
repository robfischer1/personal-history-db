"""Spotify Extended Streaming History JSON parser — yields MediaPlay records.

Source: a zip or directory containing Streaming_History_Audio_*.json
and Streaming_History_Video_*.json files.
Pure parser: no DB, no identity.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path

from phdb.records import MediaPlay, Provenance


def _yield_streaming_files(source_path: Path) -> Iterator[tuple[str, bytes]]:
    if source_path.is_file() and source_path.suffix == ".zip":
        with zipfile.ZipFile(source_path) as zf:
            for name in sorted(zf.namelist()):
                if "Streaming_History_" in name and name.endswith(".json"):
                    yield name, zf.read(name)
    elif source_path.is_dir():
        for p in sorted(source_path.rglob("Streaming_History_*.json")):
            yield str(p.relative_to(source_path)), p.read_bytes()
        for zp in sorted(source_path.glob("*.zip")):
            with zipfile.ZipFile(zp) as zf:
                for name in sorted(zf.namelist()):
                    if "Streaming_History_" in name and name.endswith(".json"):
                        yield f"{zp.name}!{name}", zf.read(name)


def parse(source_path: Path) -> Iterator[MediaPlay]:
    """Parse Spotify streaming history, yielding MediaPlay records."""
    source_str = str(source_path)

    for _relpath, json_bytes in _yield_streaming_files(source_path):
        try:
            data = json.loads(json_bytes)
        except json.JSONDecodeError:
            continue
        events = data if isinstance(data, list) else [data]
        for evt in events:
            ts = evt.get("ts")
            if not ts:
                continue

            track = str(evt.get("master_metadata_track_name") or "")
            artist = str(evt.get("master_metadata_album_artist_name") or "")
            album = str(evt.get("master_metadata_album_album_name") or "")
            episode = evt.get("episode_name")
            show = evt.get("episode_show_name")
            audiobook = evt.get("audiobook_title")
            chapter = evt.get("audiobook_chapter_title")

            if track:
                title = f"{track} — {artist}" if artist else track
                media_type = "music"
            elif episode:
                title = f"{episode} — {show}" if show else str(episode)
                media_type = "podcast"
                artist = str(show or episode)
            elif audiobook:
                title = f"{audiobook} — {chapter}" if chapter else str(audiobook)
                media_type = "audiobook"
                artist = str(audiobook)
            else:
                continue

            uri = str(
                evt.get("spotify_track_uri")
                or evt.get("spotify_episode_uri")
                or evt.get("audiobook_uri")
                or track or episode or audiobook
            )
            dedup_seed = f"spotify|{ts}|{uri}|{evt.get('ms_played')}"
            raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

            ms_played = evt.get("ms_played")

            yield MediaPlay(
                provenance=Provenance(source_path=source_str, raw_hash=raw_hash),
                media_type=media_type,
                title=title,
                date_played=str(ts),
                platform="spotify",
                artist=artist or None,
                album=album or None,
                duration_ms=int(ms_played) if ms_played is not None else None,
                platform_id=uri,
                is_skipped=bool(evt.get("skipped")),
            )
