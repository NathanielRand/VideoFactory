"""Write upstream's Clippy mascot PNGs (Clips Kitty's cat).

Video Factory keeps this only because `video/outro.py`, upstream's end card
(off by default here), animates the same geometry. Our logo and the Windows
icon come from `scripts/make_logo.py`, so this no longer writes icon.ico.

    python scripts/make_mascot.py

Outputs:
    docs/brand/mascot.png        full body — README, website, merch base
    docs/brand/mascot-head.png   head only — what the icon is cut from

The artwork itself lives in `video/mascot_art.py` — edit the numbers there and
re-run this. It moved out of here because `scripts/` is not packaged into the
frozen build, and `video/outro.py` needs the same geometry at runtime to
animate Clippy for the end card. One source, three consumers.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from video.mascot_art import render_full, render_head  # noqa: E402

BRAND = ROOT / "docs" / "brand"


def main() -> None:
    BRAND.mkdir(parents=True, exist_ok=True)

    render_full().save(BRAND / "mascot.png")
    print(f"  wrote {BRAND / 'mascot.png'}")

    head = render_head()
    head.save(BRAND / "mascot-head.png")
    print(f"  wrote {BRAND / 'mascot-head.png'}")


if __name__ == "__main__":
    main()
