"""Subject cut-out for thumbnails: the people in a frame, lifted out on a
transparent background.

This is the "pop-out" thumbnail look: the person drawn over big text or a
darkened, blurred copy of the same frame, often with a white sticker edge.
The designer draws the cut-out with the same transform as the background, so
the PNG is the FULL frame with everything but people made transparent. Laid
over its own frame at the same place it lines up exactly, and it can then be
moved or scaled on its own.

Local, no API key: an Ultralytics YOLO segmentation model, the same library
the tracker already runs. Its weights ship with the app like the tracker's
(video-factory.spec); a checkout without them downloads once, ~7 MB.

The mask comes from the model's polygons, drawn at the frame's own
resolution, so the edge is as sharp as the frame rather than the model's
low-resolution mask grid. A small feather keeps it from looking cut with
scissors.
"""

from __future__ import annotations

from pathlib import Path

SEG_WEIGHTS = "yolov8n-seg.pt"
PERSON = 0            # COCO class id
MIN_CONFIDENCE = 0.35
# People smaller than this share of the frame are background extras, not
# the subject, and a thumbnail is better without their floating heads.
MIN_AREA_SHARE = 0.01

_model = None


def _load():
    global _model
    if _model is None:
        from ultralytics import YOLO

        from core.binaries import yolo_weights

        _model = YOLO(yolo_weights(SEG_WEIGHTS))
    return _model


def person_mask(frame_bgr):
    """A uint8 mask (0/255) of every person worth keeping, frame-sized, or
    None when there is nobody in the frame."""
    import cv2
    import numpy as np

    h, w = frame_bgr.shape[:2]
    result = _load()(frame_bgr, classes=[PERSON], conf=MIN_CONFIDENCE, verbose=False)[0]
    if result.masks is None or not len(result.masks.xy):
        return None
    mask = np.zeros((h, w), dtype=np.uint8)
    kept = 0
    for polygon in result.masks.xy:
        if len(polygon) < 3:
            continue
        points = np.round(polygon).astype(np.int32)
        if cv2.contourArea(points) < MIN_AREA_SHARE * w * h:
            continue
        cv2.fillPoly(mask, [points], 255)
        kept += 1
    return mask if kept else None


def feather(mask, radius: int = 2):
    """Soften the edge by a pixel or two, keeping the inside solid."""
    import cv2

    if radius <= 0:
        return mask
    k = radius * 2 + 1
    return cv2.GaussianBlur(mask, (k, k), 0)


def cutout(frame_path: Path, target: Path) -> bool:
    """Write the frame's people to `target` as an RGBA PNG. False when there
    is nobody to cut out (a normal answer, not an error)."""
    import cv2

    frame = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
    if frame is None:
        return False
    mask = person_mask(frame)
    if mask is None:
        return False
    rgba = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
    rgba[:, :, 3] = feather(mask, max(1, round(min(frame.shape[:2]) / 540)))
    target.parent.mkdir(parents=True, exist_ok=True)
    return bool(cv2.imwrite(str(target), rgba))
