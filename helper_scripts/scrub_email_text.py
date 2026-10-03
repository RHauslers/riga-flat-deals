# -*- coding: utf-8 -*-
"""One-off cleanup: remove legacy email/unsubscribe text from generated
digests (data/digests/) and published archive copies (docs/archive/),
and delete old alert_*.html email artifacts. Idempotent."""
import glob
import os
import re

DATA_DIGESTS = "data/digests"
DOCS_ARCHIVE = "docs/archive"
UNSUB_RE = re.compile(
    r"<p class=['\"]note['\"]>To unsubscribe[^<]*</p>\s*")
CAR_FOOTER_RE = re.compile(
    r"\s*—\s*website only,\s*no email is sent for cars\.")


def _scrub_file(path):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    new = CAR_FOOTER_RE.sub("", UNSUB_RE.sub("", text))
    if new != text:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        return True
    return False


def run():
    removed_files = 0
    for path in glob.glob(os.path.join(DATA_DIGESTS, "alert_*.html")):
        os.remove(path)
        print(f"deleted {path}")
        removed_files += 1
    scrubbed = 0
    for folder in (DATA_DIGESTS, DOCS_ARCHIVE):
        for path in glob.glob(os.path.join(folder, "*.html")):
            if _scrub_file(path):
                print(f"scrubbed {path}")
                scrubbed += 1
    print(f"done: {removed_files} alert files deleted, "
          f"{scrubbed} digests scrubbed")


if __name__ == "__main__":
    run()
