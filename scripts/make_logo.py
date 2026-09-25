"""Draw the Video Factory logo: one source, many versions.

A sky-blue front frame with a play button, and two frames fanned out behind it
(the per-platform renders). Colours are the app's theme tokens (ARCHITECTURE.md
§11). Re-run after changing anything here; every output is regenerated.

    python scripts/make_logo.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
BASE = (10, 22, 40, 255)        # bg-base   #0A1628
ACCENT = (56, 189, 248, 255)    # accent    #38BDF8
STRONG = (14, 165, 233, 255)    # accent-strong #0EA5E9
MUTED = (28, 51, 84, 255)       # bg-raised #1C3354
WHITE = (241, 245, 249, 255)    # text-primary #F1F5F9


def draw(size: int, background: bool = True) -> Image.Image:
    s = 8  # supersample, then downscale for clean edges
    n = size * s
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = n / 64  # design grid: 64 units

    if background:
        d.rounded_rectangle([0, 0, n - 1, n - 1], radius=14 * u, fill=BASE)

    def frame(cx, cy, angle, fill):
        w, h = 24 * u, 34 * u  # 9:16-ish portrait frame
        layer = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle(
            [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], radius=5 * u, fill=fill
        )
        return layer.rotate(angle, center=(cx, cy), resample=Image.BICUBIC)

    img.alpha_composite(frame(24 * u, 33 * u, 16, MUTED))
    img.alpha_composite(frame(40 * u, 33 * u, -16, STRONG))
    img.alpha_composite(frame(32 * u, 32 * u, 0, ACCENT))

    # Play button on the front frame.
    d = ImageDraw.Draw(img)
    d.polygon([(28.5 * u, 25.5 * u), (28.5 * u, 38.5 * u), (39 * u, 32 * u)], fill=WHITE)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    out = {
        ROOT / "ui/src/renderer/src/assets/logo.png": draw(128),
        ROOT / "docs/brand/logo.png": draw(512),
        ROOT / "ui/build/appx/StoreLogo.png": draw(50),
        ROOT / "ui/build/appx/Square44x44Logo.png": draw(44),
        ROOT / "ui/build/appx/Square150x150Logo.png": draw(150),
    }
    for path, img in out.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path)
        print("wrote", path.relative_to(ROOT))

    big = draw(256)
    ico = ROOT / "ui/build/icon.ico"
    big.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", ico.relative_to(ROOT))

    for name, (w, h) in {"Wide310x150Logo.png": (310, 150), "SplashScreen.png": (620, 300)}.items():
        canvas = Image.new("RGBA", (w, h), BASE)
        mark = draw(int(h * 0.8), background=False)
        canvas.alpha_composite(mark, ((w - mark.width) // 2, (h - mark.height) // 2))
        path = ROOT / "ui/build/appx" / name
        canvas.save(path)
        print("wrote", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
