"""The tray icon, drawn at run time with Pillow (HDW-504). No image file ships.

Green: the daemon is up and there is nothing to say. Amber: a wall is active, the last decision
scored below the floor, or no provider can be reached (no key set and none works without one).
Red: the daemon is not running, or the last routed request failed. Private Mode draws a purple
lock *over* whichever colour is current, so amber with a purple lock is a real state.
"""

from __future__ import annotations

from typing import Final

from PIL import Image, ImageDraw

__all__ = [
    "AMBER_RGB",
    "CENTRE_PROBE",
    "GREEN_RGB",
    "LOCK_PROBE",
    "PURPLE_RGB",
    "RED_RGB",
    "SIZE",
    "render",
]

SIZE: Final = 64
GREEN_RGB: Final = (46, 158, 79)
AMBER_RGB: Final = (224, 163, 0)
RED_RGB: Final = (198, 40, 40)
PURPLE_RGB: Final = (123, 31, 162)

# Probe points the tests read: the centre of the disc, and a point inside the lock body.
CENTRE_PROBE: Final = (SIZE // 2, SIZE // 2)
LOCK_PROBE: Final = (47, 50)

_PALETTE: Final = {"green": GREEN_RGB, "amber": AMBER_RGB, "red": RED_RGB}
_DISC: Final = (2, 2, SIZE - 3, SIZE - 3)
_LOCK_BODY: Final = (36, 38, 58, 60)
_LOCK_SHACKLE: Final = (40, 24, 54, 44)


def render(color: str, *, private: bool = False) -> Image.Image:
    """A ``SIZE`` square RGBA icon in ``color``, with the Private Mode lock drawn over it."""
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse(_DISC, fill=_PALETTE[color])
    if private:
        # Drawn after the disc and kept off the centre, so the base colour still shows through.
        draw.arc(_LOCK_SHACKLE, start=180, end=360, fill=PURPLE_RGB, width=4)
        draw.rounded_rectangle(_LOCK_BODY, radius=3, fill=PURPLE_RGB)
    return image
