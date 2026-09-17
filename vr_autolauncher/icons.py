# -*- coding: utf-8 -*-
import os
import subprocess

from vr_autolauncher import osdeps
from vr_autolauncher.engine import ST_ACTIVE, ST_CLOSED, ST_DETECTED, ST_WAITING

STYLE = {
    ST_WAITING: ("#4a90d9", "VR"),
    ST_DETECTED: ("#f5a623", "VR"),
    ST_ACTIVE: ("#27ae60", "OK"),
    ST_CLOSED: ("#e74c3c", "--"),
}

_font_path = None


def _find_font():
    global _font_path
    if _font_path is not None:
        return _font_path
    candidates = [r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf"]
    if not osdeps.IS_WINDOWS:
        try:
            found = subprocess.check_output(["fc-match", "-f", "%{file}", "sans:bold"],
                                            stderr=subprocess.DEVNULL, timeout=5).decode().strip()
            candidates.insert(0, found)
        except Exception:
            pass
    _font_path = next((p for p in candidates if p and os.path.exists(p)), "")
    return _font_path


def make_icon(state, size=64):
    from PIL import Image, ImageDraw, ImageFont

    color, text = STYLE[state]
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2, 2, size - 2, size - 2], fill=color)
    font = None
    if _find_font():
        try:
            font = ImageFont.truetype(_find_font(), int(size * 0.36))
            try:
                font.set_variation_by_name("Bold")  # variable fonts (Noto on Fedora)
            except Exception:
                pass
        except Exception:
            font = None
    if font is None:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]), text, fill="white", font=font)
    return img


def write_icons(directory):
    """Render one PNG per state as <directory>/vr-autolauncher-<state>.png; returns the names."""
    names = {}
    for state in STYLE:
        name = "vr-autolauncher-" + state
        make_icon(state).save(os.path.join(directory, name + ".png"))
        names[state] = name
    return names
