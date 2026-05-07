"""Generate test fixture: zip containing FB connections HTML files."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
OUTPUT = HERE / "facebook_connections_test.zip"

YOUR_FRIENDS_HTML = """\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Your friends</title></head>
<body>
<div class="_a706">
<h1>Your friends</h1>
<section class="_a6-g">
<h2 class="_a6-h">Alice Johnson</h2>
<div class="_a6-p"></div>
<footer class="_a6-o"><div class="_a72d">Jan 15, 2020 3:45:30 pm</div></footer>
</section>
<section class="_a6-g">
<h2 class="_a6-h">Bob Smith</h2>
<div class="_a6-p"></div>
<footer class="_a6-o"><div class="_a72d">Mar 22, 2018 10:15:00 am</div></footer>
</section>
<section class="_a6-g">
<h2 class="_a6-h">Charlie Davis</h2>
<div class="_a6-p"></div>
<footer class="_a6-o"><div class="_a72d">Dec 1, 2019 8:30:00 pm</div></footer>
</section>
</div>
</body>
</html>
"""

REMOVED_FRIENDS_HTML = """\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Removed friends</title></head>
<body>
<div class="_a706">
<h1>Removed friends</h1>
<section class="_a6-g">
<h2 class="_a6-h">Dave Wilson</h2>
<div class="_a6-p"></div>
<footer class="_a6-o"><div class="_a72d">Jun 10, 2021 2:00:00 pm</div></footer>
</section>
</div>
</body>
</html>
"""

SENT_FRIEND_REQUESTS_HTML = """\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Sent friend requests</title></head>
<body>
<div class="_a706">
<h1>Sent friend requests</h1>
<section class="_a6-g">
<h2 class="_a6-h">Eve Martinez</h2>
<div class="_a6-p"></div>
<footer class="_a6-o"><div class="_a72d">Apr 5, 2022 11:00:00 am</div></footer>
</section>
</div>
</body>
</html>
"""


def main() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("connections/friends/your_friends.html", YOUR_FRIENDS_HTML)
        zf.writestr("connections/friends/removed_friends.html", REMOVED_FRIENDS_HTML)
        zf.writestr("connections/friends/sent_friend_requests.html", SENT_FRIEND_REQUESTS_HTML)

    OUTPUT.write_bytes(buf.getvalue())
    print(f"Wrote: {OUTPUT}")


if __name__ == "__main__":
    main()
