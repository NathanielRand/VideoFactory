"""Thumbnail candidates, generated on this machine.

The picker already offers three frames, at a quarter, half and three quarters
of the clip. That is what YouTube Studio does and it is often not enough: the
frame at the halfway mark is as likely to be a blink or a turned head as a
face. This generates candidates instead — frames where somebody is actually
facing the camera, cropped to 16:9 around them, with the clip's own hook line
across the bottom.

No cloud call and no API key. Faces come from the Haar cascades already
bundled for the tracker, frames from the bundled FFmpeg's decoder through
OpenCV, and the type from a font already on the machine.

**Every step degrades rather than fails**, because a thumbnail is a
convenience and the existing picker is right there:

  * no cascade on disk -> centre crop, no face search
  * no usable font -> the image without text, which is still a thumbnail
  * a clip that yields no readable frame -> an empty list, and the caller
    shows what it showed before

The pure geometry and text fitting live in functions that touch neither
OpenCV nor Pillow, so they are tested on a CI runner that has neither.
"""

from functools import lru_cache
from pathlib import Path

# YouTube's own recommendation, and what Studio displays: 1280x720, 16:9.
WIDTH, HEIGHT = 1280, 720
RATIO = WIDTH / HEIGHT
MAX_BYTES = 2 * 1024 * 1024   # YouTube's limit, enforced in publish/images.py too
MAX_LINE = 26                 # characters per line of burned text
MAX_LINES = 2

# Bold faces worth trying, in order, ending with whatever Pillow can always
# give us. Unlike video/outro.py this must never raise: the end card carries
# the product's name and has to be in the right face, a thumbnail does not.
FONT_CANDIDATES = (
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)


def candidate_times(duration: float, count: int = 6) -> list[float]:
    """Where to look for a good frame.

    The first and last tenth are skipped: a clip starts on a sentence boundary,
    which is often mid-turn, and ends on the end card, which is not a
    thumbnail of anybody's video.
    """
    if duration <= 0 or count <= 0:
        return []
    first, last = duration * 0.1, duration * 0.9
    if last <= first:
        return [duration / 2]
    if count == 1:
        return [(first + last) / 2]
    step = (last - first) / (count - 1)
    return [round(first + step * i, 3) for i in range(count)]


def crop_box(
    width: int, height: int, face: tuple[int, int, int, int] | None
) -> tuple[int, int, int, int]:
    """The widest 16:9 box that fits, centred on the face when there is one.

    Returned as (left, top, right, bottom) in pixels, always inside the frame.
    A vertical clip is the normal input here, so the box is usually the full
    width and the interesting question is only how high up it sits: put the
    face a little above centre, the way a person frames a photograph.
    """
    if width <= 0 or height <= 0:
        return (0, 0, max(width, 1), max(height, 1))

    box_w, box_h = width, round(width / RATIO)
    if box_h > height:                      # a wide source: limit by height
        box_h, box_w = height, round(height * RATIO)

    if face:
        fx, fy, fw, fh = face
        cx, cy = fx + fw / 2, fy + fh / 2
        # Faces sit above the middle of a good thumbnail, not dead centre.
        top = round(cy - box_h * 0.42)
        left = round(cx - box_w / 2)
    else:
        left = round((width - box_w) / 2)
        top = round((height - box_h) / 2)

    left = max(0, min(left, width - box_w))
    top = max(0, min(top, height - box_h))
    return (left, top, left + box_w, top + box_h)


def wrap_title(text: str, max_line: int = MAX_LINE, max_lines: int = MAX_LINES) -> list[str]:
    """The hook, as the one or two short lines a thumbnail can carry.

    Long hooks are cut at a word with an ellipsis rather than wrapped into a
    paragraph: a thumbnail is read at a glance and at thumbnail size, so a
    third line is unreadable text sitting on top of the picture.
    """
    words = " ".join((text or "").split()).split(" ")
    if not words or words == [""]:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_line:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if not lines:
        return []
    # Something was left over: mark the cut so it does not read as the whole
    # sentence ending oddly.
    consumed = len(" ".join(lines).split(" "))
    if consumed < len(words):
        last = lines[-1]
        lines[-1] = (last[: max_line - 1].rstrip() + "…") if len(last) >= max_line else last + "…"
    return lines


def _font(size: int):
    """A bold face at this size, or Pillow's built-in as a last resort."""
    from PIL import ImageFont

    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


@lru_cache(maxsize=8)
def _cascade(name: str):
    """A loaded cascade, or None. Loaded once: building the classifier is a
    third of the cost of looking at a frame, and a scan looks at dozens."""
    import cv2

    from core.binaries import haar_cascade

    path = haar_cascade(name)
    if not path:
        return None
    classifier = cv2.CascadeClassifier(path)
    return None if classifier.empty() else classifier


def _faces(frame):
    """Face boxes in a frame, biggest first, or [] when detection is not
    available. Never raises: see core/binaries.haar_cascade for why a missing
    cascade is a normal condition rather than a fault."""
    import cv2

    classifier = _cascade("haarcascade_frontalface_default.xml")
    if classifier is None:
        return []
    try:
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        found = classifier.detectMultiScale(grey, scaleFactor=1.1, minNeighbors=6,
                                            minSize=(60, 60))
    except cv2.error:
        return []
    return sorted(([int(v) for v in f] for f in found), key=lambda f: f[2] * f[3], reverse=True)


def _sharpness(frame) -> float:
    """How much detail a frame has. A motion-blurred or near-black frame is a
    bad thumbnail however well it is cropped."""
    import cv2

    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


def _blockiness(grey) -> float:
    """How visibly the picture is built of 8x8 compression blocks, 0 for none.

    Heavily compressed or upscaled sources step at block edges: the change
    across a multiple-of-8 column is larger than the change inside a block.
    Measured on columns only, which is enough to tell and cheap."""
    import numpy as np

    g = grey.astype("float32")
    if g.shape[1] < 32:
        return 0.0
    d = np.abs(np.diff(g, axis=1)).mean(axis=0)
    edge = d[7::8].mean()
    inner = np.delete(d, np.arange(7, len(d), 8)).mean()
    return float(max(0.0, edge / max(inner, 1e-3) - 1.0))




def _has_eyes(grey, face) -> bool | None:
    """Two eyes visible in the upper half of the face, so not a blink or a
    turned head. None when the cascade is unavailable (no opinion)."""
    import cv2

    classifier = _cascade("haarcascade_eye.xml")
    if classifier is None:
        return None
    x, y, w, h = face
    roi = grey[y: y + int(h * 0.6), x: x + w]
    if roi.size == 0:
        return None
    try:
        eyes = classifier.detectMultiScale(roi, scaleFactor=1.1, minNeighbors=6,
                                           minSize=(max(12, w // 10), max(12, w // 10)))
    except cv2.error:
        return None
    return len(eyes) >= 2


def _expression(grey, face) -> float:
    """0..1, how animated the face looks: a smile or an open mouth. Haar's
    smile cascade is noisy, so it is a small bonus and never a gate."""
    import cv2

    classifier = _cascade("haarcascade_smile.xml")
    if classifier is None:
        return 0.0
    x, y, w, h = face
    roi = grey[y + int(h * 0.55): y + h, x: x + w]
    if roi.size == 0:
        return 0.0
    try:
        found = classifier.detectMultiScale(roi, scaleFactor=1.5, minNeighbors=18,
                                            minSize=(max(20, w // 4), max(10, w // 8)))
    except cv2.error:
        return 0.0
    return 1.0 if len(found) else 0.0


CRISP_REF = 150.0   # Laplacian variance of a face crop that is plainly sharp


def _face_size_score(area: float) -> float:
    """0..1. A face that fills a few percent of the frame is a face in a scene;
    it has to be big to carry a thumbnail read at phone size. Past about a
    third of the frame it is a nose."""
    if area <= 0:
        return 0.0
    if area < 0.08:
        return area / 0.08
    if area <= 0.35:
        return 1.0
    return max(0.4, 1.0 - (area - 0.35) * 2.0)


def score_frame(
    sharpness: float,
    face_area_fraction: float,
    brightness: float,
    *,
    face_sharpness: float | None = None,
    eyes_open: bool | None = None,
    expression: float = 0.0,
    blockiness: float = 0.0,
    relative_sharpness: float = 1.0,
) -> float:
    """How good a thumbnail this frame would make.

    A visible, well-sized face dominates because a face is what gets clicked.
    Sharpness is measured on the face when there is one, and it VETOES: a
    blurry or pixelated frame is scaled down whatever else it has going for it
    (`relative_sharpness` is this frame against the clip's median, so a soft
    source is not marked down for being soft everywhere). Open eyes and an
    animated expression are bonuses. Pure arithmetic, so the weighting is
    testable without decoding a video.
    """
    if brightness < 18 or brightness > 242:   # near black or blown out
        return 0.0
    face = _face_size_score(face_area_fraction)
    detail = face_sharpness if (face_sharpness is not None and face_area_fraction > 0) else sharpness
    crisp = min(max(detail, 0.0) / CRISP_REF, 1.0)
    base = face * 55.0 + crisp * 30.0 + min(max(sharpness, 0.0), 500.0) / 500.0 * 10.0
    if face_area_fraction > 0:
        if eyes_open is True:
            base += 8.0
        elif eyes_open is False:
            base *= 0.6
        base += 7.0 * min(max(expression, 0.0), 1.0)
    base *= 1.0 - min(max(blockiness, 0.0), 0.6)
    return base * min(1.0, max(0.25, relative_sharpness))


def analyse_frame(frame) -> dict:
    """Everything score_frame needs for one BGR frame. `face` is (x, y, w, h)
    on a copy scaled by `scale`."""
    import cv2

    h, w = frame.shape[:2]
    scale = min(1.0, ANALYSE_WIDTH / max(w, 1))
    small = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale)))) if scale < 1 else frame
    grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    faces = _faces(small)
    face = faces[0] if faces else None
    sh, sw = small.shape[:2]
    out = {
        "sharp": float(cv2.Laplacian(grey, cv2.CV_64F).var()),
        "brightness": float(small.mean()),
        "blockiness": _blockiness(grey),
        "area": (face[2] * face[3]) / float(sw * sh) if face else 0.0,
        "face": face,
        "face_sharp": None,
        "eyes": None,
        "expression": 0.0,
        "scale": scale,
    }
    if face:
        x, y, fw, fh = face
        roi = grey[y: y + fh, x: x + fw]
        if roi.size:
            out["face_sharp"] = float(cv2.Laplacian(roi, cv2.CV_64F).var())
        out["eyes"] = _has_eyes(grey, face)
        out["expression"] = _expression(grey, face)
    return out


def rank_frames(rows: list[dict], min_gap: float = 1.0) -> list[dict]:
    """rows: analyse_frame dicts plus 't'. Scores each against the clip's own
    median sharpness and returns the usable ones, best first, no two closer
    than `min_gap` seconds (six near-identical frames from one moment are one
    choice, not six)."""
    if not rows:
        return []
    ordered = sorted(r["sharp"] for r in rows)
    median = ordered[len(ordered) // 2] or 1.0
    for r in rows:
        r["score"] = score_frame(
            r["sharp"], r["area"], r["brightness"],
            face_sharpness=r.get("face_sharp"), eyes_open=r.get("eyes"),
            expression=r.get("expression", 0.0), blockiness=r.get("blockiness", 0.0),
            relative_sharpness=r["sharp"] / median,
        )
    out: list[dict] = []
    for r in sorted(rows, key=lambda r: (r["score"], r["sharp"]), reverse=True):
        if r["score"] <= 0:
            continue
        if all(abs(r["t"] - o["t"]) >= min_gap for o in out):
            out.append(r)
    return out


SAMPLE_STEP = 0.5    # seconds between looked-at frames, at most
MAX_SAMPLES = 28
REFINE_SPAN = 0.3    # seconds either side of each leader, looked at for a crisper frame
ANALYSE_WIDTH = 960


def pick_frames(video: Path, count: int = 6, start: float = 0.0, duration: float | None = None) -> list[dict]:
    """The best frames of `duration` seconds of `video` from `start`, as
    analyse_frame dicts with `t` relative to `start`, best first.

    One seek, then the clip is decoded straight through and only every Nth
    frame is looked at: seeking to each sample costs a decode from the nearest
    keyframe every time, which on a long source made this take half a minute.
    The leaders are then re-checked frame by frame beside where they were
    found, because a decoder's frame beside a great one is often the sharper."""
    import cv2

    from video.capture import video_capture

    with video_capture(Path(video), required=False) as cap:
        if cap is None:
            return []
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / fps if fps else 0.0
        length = duration if duration else max(0.0, total - start)
        if length <= 0:
            return []
        lo, hi = length * 0.1, length * 0.9      # clips open mid-turn and end on the end card
        if hi <= lo:
            lo, hi = 0.0, length
        step = max(SAMPLE_STEP, (hi - lo) / MAX_SAMPLES)
        rows: dict[float, dict] = {}

        def scan(from_t: float, to_t: float, every: float) -> None:
            cap.set(cv2.CAP_PROP_POS_MSEC, (start + max(0.0, from_t)) * 1000.0)
            gap = max(1, round(fps * every))
            n = 0
            t = from_t
            while t <= to_t:
                if n % gap == 0:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        break
                    key = round(t, 2)
                    if key not in rows:
                        r = analyse_frame(frame)
                        r["t"] = key
                        rows[key] = r
                elif not cap.grab():
                    break
                n += 1
                t = from_t + n / fps

        scan(lo, hi, step)
        for lead in rank_frames(list(rows.values()))[:3]:
            scan(max(0.0, lead["t"] - REFINE_SPAN), min(length, lead["t"] + REFINE_SPAN), 0.15)
        return rank_frames(list(rows.values()))[:count]


def generate(video_path: Path, hook: str, targets: list[Path]) -> list[Path]:
    """Write up to len(targets) thumbnail candidates. Returns what was written.

    Best candidate first. An empty list means nothing usable came out, which
    is a normal outcome and not an error.
    """
    # The cheap answers come first, and deliberately before the imports: asking
    # for no candidates, or naming a file that is not there, is answerable
    # without OpenCV, and a machine without it should still get the honest
    # empty list rather than an ImportError. CI is exactly that machine.
    if not targets or not Path(video_path).exists():
        return []

    import cv2
    from PIL import Image, ImageDraw

    from video.capture import video_capture

    picks = pick_frames(Path(video_path), count=len(targets))
    if not picks:
        return []
    scored: list[tuple[float, object, tuple | None]] = []
    with video_capture(Path(video_path), required=False) as cap:
        if cap is None:
            return []
        for r in picks:
            cap.set(cv2.CAP_PROP_POS_MSEC, r["t"] * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            face = r["face"]
            if face and r["scale"] < 1:      # the box was found on a smaller copy
                face = tuple(int(v / r["scale"]) for v in face)
            scored.append((r["score"], frame.copy(), face))
    if not scored:
        return []
    scored.sort(key=lambda row: row[0], reverse=True)

    lines = wrap_title(hook)
    written: list[Path] = []
    for (_score, frame, face), target in zip(scored, targets):
        left, top, right, bottom = crop_box(frame.shape[1], frame.shape[0], face)
        cropped = frame[top:bottom, left:right]
        image = Image.fromarray(cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB)).resize(
            (WIDTH, HEIGHT), Image.LANCZOS
        )
        if lines:
            _draw_title(ImageDraw.Draw(image, "RGBA"), lines)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Quality stepped down only if needed: YouTube refuses over 2 MB, and a
        # 1280x720 JPEG is nowhere near it until the picture is very noisy.
        for quality in (88, 75, 60):
            image.save(target, "JPEG", quality=quality, optimize=True)
            if target.stat().st_size <= MAX_BYTES:
                break
        written.append(target)
    return written


def _draw_title(draw, lines: list[str]) -> None:
    """The hook across the bottom, on a band dark enough to read over
    anything. Thumbnails are viewed at a fraction of full size, so the type is
    large and the contrast is not subtle."""
    size = 68 if len(lines) == 1 else 58
    font = _font(size)
    if font is None:
        return
    gap = 12
    heights = []
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font, stroke_width=3)
        heights.append(box[3] - box[1])
    block = sum(heights) + gap * (len(lines) - 1)
    band_top = HEIGHT - block - 64
    draw.rectangle([(0, band_top - 26), (WIDTH, HEIGHT)], fill=(0, 0, 0, 140))

    y = band_top
    for line, line_height in zip(lines, heights):
        box = draw.textbbox((0, 0), line, font=font, stroke_width=3)
        x = (WIDTH - (box[2] - box[0])) / 2
        draw.text((x, y - box[1]), line, font=font, fill=(255, 255, 255),
                  stroke_width=3, stroke_fill=(0, 0, 0))
        y += line_height + gap
