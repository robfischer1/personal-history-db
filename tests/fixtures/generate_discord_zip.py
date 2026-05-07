"""Generate a synthetic Discord package.zip for adapter testing.

Run once:  python tests/fixtures/generate_discord_zip.py
Output:    tests/fixtures/discord/package.zip
"""

import json
import zipfile
from pathlib import Path

OUT_DIR = Path(__file__).parent / "discord"
OUT_DIR.mkdir(exist_ok=True)
ZIP_PATH = OUT_DIR / "package.zip"

USER_JSON = {
    "id": "111222333444555666",
    "username": "testuser",
    "global_name": "Test User",
}

INDEX_JSON = {
    "900000000000000001": "Direct Message with JaneDoe#1234",
    "900000000000000002": "gaming-chat",
    "900000000000000003": "Direct Message with BobSmith#5678",
}

# Channel 1: DM with JaneDoe, 3 messages
CH1_META = {
    "id": "900000000000000001",
    "type": "DM",
    "name": None,
    "recipients": ["111222333444555666", "777888999000111222"],
}
CH1_MSGS = [
    {"ID": "1000000000000000001", "Timestamp": "2024-01-15 14:30:00",
     "Contents": "Hey Jane, how are you?", "Attachments": ""},
    {"ID": "1000000000000000002", "Timestamp": "2024-01-15 14:35:00",
     "Contents": "Want to grab coffee tomorrow?", "Attachments": ""},
    {"ID": "1000000000000000003", "Timestamp": "2024-01-16 10:00:00",
     "Contents": "Check out this photo",
     "Attachments": "https://cdn.discordapp.com/attachments/900/1000/sunset.jpg"},
]

# Channel 2: Guild channel with 2 messages
CH2_META = {
    "id": "900000000000000002",
    "type": "GUILD_TEXT",
    "name": "gaming-chat",
    "guild": {"id": "888000000000000001", "name": "Cool Server"},
}
CH2_MSGS = [
    {"ID": "1000000000000000004", "Timestamp": "2024-02-01 20:00:00",
     "Contents": "GG everyone!", "Attachments": ""},
    {"ID": "1000000000000000005", "Timestamp": "2024-02-01 20:05:00",
     "Contents": "",
     "Attachments": "https://cdn.discordapp.com/attachments/900/1000/clip.mp4 https://cdn.discordapp.com/attachments/900/1000/screenshot.png"},
]

# Channel 3: DM with BobSmith, 1 message (for resume/idempotent testing)
CH3_META = {
    "id": "900000000000000003",
    "type": "DM",
    "name": None,
    "recipients": ["111222333444555666", "333444555666777888"],
}
CH3_MSGS = [
    {"ID": "1000000000000000006", "Timestamp": "2024-03-01 09:00:00",
     "Contents": "Hey Bob, long time no talk!", "Attachments": ""},
]


def main() -> None:
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Account/user.json", json.dumps(USER_JSON))
        zf.writestr("Messages/index.json", json.dumps(INDEX_JSON))

        for cid, meta, msgs in [
            ("900000000000000001", CH1_META, CH1_MSGS),
            ("900000000000000002", CH2_META, CH2_MSGS),
            ("900000000000000003", CH3_META, CH3_MSGS),
        ]:
            zf.writestr(f"Messages/c{cid}/channel.json", json.dumps(meta))
            zf.writestr(f"Messages/c{cid}/messages.json", json.dumps(msgs))

    print(f"Written: {ZIP_PATH}  ({ZIP_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
