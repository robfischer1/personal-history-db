#!/usr/bin/env python3
"""Generate a synthetic facebook residuals zip for testing.

Creates test_residuals.zip with two HTML files:
  - your_facebook_activity/comments_and_reactions/comments.html  (h2 pattern)
  - your_facebook_activity/comments_and_reactions/likes_and_reactions.html (table pattern)
"""
from __future__ import annotations

import zipfile
from pathlib import Path

H2_HTML = """\
<!DOCTYPE html>
<html><head><title>Comments</title></head><body>
<h1>Comments</h1>
<section class="_a6-g">
  <h2 class="_a6-h">Rob commented on a post</h2>
  <div class="_a6-p"><div>This is my first comment body</div></div>
  <footer class="_a6-o">
    <a href="https://facebook.com/post/123">
      <div class="_a72d">Dec 06, 2008 10:18:27 pm</div>
    </a>
  </footer>
</section>
<section class="_a6-g">
  <h2 class="_a6-h">Rob replied to a comment</h2>
  <div class="_a6-p"><div>Second comment body here</div></div>
  <footer class="_a6-o">
    <div class="_a72d">Aug 16, 2010 5:57:27 pm</div>
  </footer>
</section>
</body></html>
"""

TABLE_HTML = """\
<!DOCTYPE html>
<html><head><title>Likes and Reactions</title></head><body>
<h1>Likes and Reactions</h1>
<section class="_a6-g">
  <div class="_a6-p">
    <section class="_a6-g">
      <table>
        <tr><td class="_a6_q">Reaction</td><td class="_a6_r">LIKE</td></tr>
        <tr><td class="_a6_q">Title</td><td class="_a6_r">Cool photo album</td></tr>
      </table>
    </section>
  </div>
  <footer class="_a6-o">
    <div class="_a72d">Jan 15, 2012 3:22:10 pm</div>
  </footer>
</section>
<section class="_a6-g">
  <div class="_a6-p">
    <section class="_a6-g">
      <table>
        <tr><td class="_a6_q">Reaction</td><td class="_a6_r">HAHA</td></tr>
        <tr><td class="_a6_q">Title</td><td class="_a6_r">Funny meme page</td></tr>
      </table>
    </section>
  </div>
  <footer class="_a6-o">
    <div class="_a72d">Mar 01, 2015 8:00:00 am</div>
  </footer>
</section>
</body></html>
"""


def main() -> None:
    out = Path(__file__).parent / "test_residuals.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "your_facebook_activity/comments_and_reactions/comments.html",
            H2_HTML,
        )
        zf.writestr(
            "your_facebook_activity/comments_and_reactions/likes_and_reactions.html",
            TABLE_HTML,
        )
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
