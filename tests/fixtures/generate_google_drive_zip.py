"""Generate a synthetic Google Drive takeout zip for adapter testing.

Run once:  python tests/fixtures/generate_google_drive_zip.py
Output:    tests/fixtures/google_drive/takeout.zip
"""

import json
import zipfile
from pathlib import Path

OUT_DIR = Path(__file__).parent / "google_drive"
OUT_DIR.mkdir(exist_ok=True)
ZIP_PATH = OUT_DIR / "takeout.zip"

FILES = {
    "Takeout/Drive/My Files/test.txt": b"Hello world from a text file.",
    "Takeout/Drive/My Files/data.json": json.dumps(
        {"name": "test", "values": [1, 2, 3]}
    ).encode(),
    "Takeout/Drive/My Files/notes.csv": b"name,score\nAlice,95\nBob,87\n",
    "Takeout/Drive/My Files/readme.md": b"# Project Readme\n\nSome markdown content.",
    "Takeout/Drive/My Files/photo.jpg": b"\xff\xd8\xff\xe0JFIF-fake-jpeg-data",
    "Takeout/Drive/Projects/Deep/nested.txt": b"Nested file content.",
    "Takeout/Drive/Trash/deleted.txt": b"This should be skipped.",
    "Takeout/Drive/My Files/Copy of template.txt": b"This should be skipped too.",
}


def main() -> None:
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in FILES.items():
            zf.writestr(name, data)

    print(f"Written: {ZIP_PATH}  ({ZIP_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
