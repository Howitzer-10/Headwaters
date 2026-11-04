"""The tray icon, drawn at run time with Pillow (HDW-504). No image file ships.

Green: the daemon is up and there is nothing to say. Amber: a wall is active, the last decision
scored below the floor, or no provider can be reached (no key set and none works without one).
Red: the daemon is not running, or the last routed request failed. Private Mode draws a purple
lock *over* whichever colour is current, so amber with a purple lock is a real state.

Inside the disc sits the Headwaters mark: a white droplet, apex up, standing on its round belly,
with a wave cut across it as a waterline.
"""

from __future__ import annotations

import math
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

# The mark is white on every status colour. It is drawn four times oversized and scaled back down
# so its curves stay smooth at 64 px, without an image file.
_MARK_RGB: Final = (255, 255, 255, 255)
_SUPERSAMPLE: Final = 4
_STEPS: Final = 240  # points per curve, plenty at 4x oversampling

# Mark geometry as fractions of SIZE, with the pixel value at SIZE == 64 in the comment.
_APEX: Final = (0.492, 0.141)  # 31.5, 9: the point of the droplet
_BELLY: Final = (0.492, 0.617)  # 31.5, 39.5: centre of the round belly it stands on
_BELLY_RADIUS: Final = 0.195  # 12.5
_SHOULDER_BOW: Final = 0.025  # 1.6: how far the two shoulders bow outwards
_OUTLINE: Final = 0.100  # 6.4: width of the droplet's stroke
_WAVE_CREST: Final = 0.406  # 26.0: the waterline's height where it crosses the middle
_WAVE_AMPLITUDE: Final = 0.023  # 1.5: how far the waterline falls away towards the sides
_WAVE_SPAN: Final = 0.188  # 12.0: how much of the drop's width the wave crosses


def _px(fraction: float) -> float:
    return fraction * SIZE


def _droplet_outline() -> list[tuple[float, float]]:
    """A closed droplet: a circular belly, then two curved shoulders rising to the apex."""
    cx, cy = _px(_BELLY[0]), _px(_BELLY[1])
    radius = _px(_BELLY_RADIUS)
    apex = (_px(_APEX[0]), _px(_APEX[1]))
    # Half-angle of the belly circle that the shoulders leave free for the apex.
    alpha = math.acos(radius / (cy - apex[1]))
    shoulders = (
        (cx - radius * math.sin(alpha), cy - radius * math.cos(alpha)),
        (cx + radius * math.sin(alpha), cy - radius * math.cos(alpha)),
    )
    points: list[tuple[float, float]] = []
    phi_start, phi_end = -math.pi / 2 + alpha, 3 * math.pi / 2 - alpha
    for i in range(_STEPS + 1):
        phi = phi_start + (phi_end - phi_start) * i / _STEPS
        points.append((cx + radius * math.cos(phi), cy + radius * math.sin(phi)))
    bow = _px(_SHOULDER_BOW)
    for start, end, bow_sign in ((shoulders[0], apex, -1.0), (apex, shoulders[1], 1.0)):
        control = ((start[0] + end[0]) / 2 + bow_sign * bow, (start[1] + end[1]) / 2)
        for i in range(1, _STEPS):
            t = i / _STEPS
            u = 1 - t
            points.append(
                (
                    u * u * start[0] + 2 * u * t * control[0] + t * t * end[0],
                    u * u * start[1] + 2 * u * t * control[1] + t * t * end[1],
                )
            )
    return points


def _waterline() -> list[tuple[float, float]]:
    """One cosine swell, crossing the middle of the droplet: the wave that makes it headwaters."""
    cx = _px(_BELLY[0])
    half_span = _px(_WAVE_SPAN) / 2
    amplitude = _px(_WAVE_AMPLITUDE)
    crest = _px(_WAVE_CREST)
    return [
        (cx + half_span * t, crest + amplitude * (1 - math.cos(math.pi * t)))
        for t in (-1 + 2 * i / _STEPS for i in range(_STEPS + 1))
    ]


def _scaled(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return [(x * _SUPERSAMPLE, y * _SUPERSAMPLE) for x, y in points]


def _crown_mask() -> Image.Image:
    """The part of the droplet above the waterline, as a 0/255 fill mask."""
    edge = SIZE * _SUPERSAMPLE
    droplet = Image.new("L", (edge, edge), 0)
    ImageDraw.Draw(droplet).polygon(_scaled(_droplet_outline()), fill=255)
    above = Image.new("L", (edge, edge), 0)
    ImageDraw.Draw(above).polygon([*_scaled(_waterline()), (edge, 0), (0, 0)], fill=255)
    return Image.composite(droplet, Image.new("L", (edge, edge), 0), above)


def _mark() -> Image.Image:
    """The droplet, antialiased, on its own transparent layer.

    It is a thick *outline* with only the part above the waterline filled, deliberately not a
    solid shape. A solid droplet would paint over the middle of the icon, and ``CENTRE_PROBE``
    reads that pixel to prove the tray still shows green, amber or red. Leaving the middle open
    keeps that probe honest by construction -- not by luck of where a wave happens to fall -- and
    the status colour showing through the droplet reads as water in it. The lower right stays
    open as well, so the purple Private Mode lock lands on disc and droplet edge, never on a
    white area that would swallow it.
    """
    edge = SIZE * _SUPERSAMPLE
    layer = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
    outline = _scaled(_droplet_outline())
    ImageDraw.Draw(layer).line(
        outline + outline[:1],
        fill=_MARK_RGB,
        width=round(_px(_OUTLINE) * _SUPERSAMPLE),
        joint="curve",
    )
    layer.paste(_MARK_RGB, (0, 0), _crown_mask())
    return layer.resize((SIZE, SIZE), Image.LANCZOS)


def render(color: str, *, private: bool = False) -> Image.Image:
    """A ``SIZE`` square RGBA icon in ``color``, with the Private Mode lock drawn over it."""
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse(_DISC, fill=_PALETTE[color])
    image.alpha_composite(_mark())
    if private:
        # Drawn after the disc and kept off the centre, so the base colour still shows through.
        draw.arc(_LOCK_SHACKLE, start=180, end=360, fill=PURPLE_RGB, width=4)
        draw.rounded_rectangle(_LOCK_BODY, radius=3, fill=PURPLE_RGB)
    return image
