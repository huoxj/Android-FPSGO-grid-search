import subprocess
import os
import cv2
import numpy as np

from utils.adb import adb_shell

UiSpec = tuple[cv2.typing.MatLike, tuple[int, int, int, int], float]

def shot():
    raw = subprocess.run(
        ["adb", "exec-out", "screencap"],
        capture_output=True, timeout=20, check=True
    ).stdout
    w, h, _, _ = np.frombuffer(raw[:16], dtype="<u4")
    img = np.frombuffer(
        raw[16:16 + w * h * 4], dtype=np.uint8
    ).reshape(h, w, 4)
    return cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)

def tap(x, y):
    adb_shell(f"input tap {int(x)} {int(y)}")

def find(img, spec: UiSpec) -> tuple[int, int] | None:
    """Locate a template in an image
    Returns the center (x, y) or None if not found.

    TM_CCOEFF_NORMED (mean-subtracted) match -> center (x, y) or None.
    Defensive: a region slice smaller than the template (portrait/black boot
    frame in a landscape-locked game) means 'not this screen' -> None, so the
    caller keeps polling instead of crashing."""
    template, region, thresh = spec

    x0, y0, x1, y1 = region
    h, w = img.shape[:2]
    y1, x1 = min(y1, h), min(x1, w)
    if y1 - y0 < 1 or x1 - x0 < 1:
        return None
    sl = img[y0:y1, x0:x1]
    th, tw = template.shape[:2]
    if sl.shape[0] < th or sl.shape[1] < tw:
        return None
    g = cv2.cvtColor(sl, cv2.COLOR_BGR2GRAY)
    tg = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    res = cv2.matchTemplate(g, tg, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    if score < thresh:
        return None
    return (x0 + loc[0] + tg.shape[1] // 2, y0 + loc[1] + tg.shape[0] // 2)

