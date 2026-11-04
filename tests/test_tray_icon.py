"""The Headwaters mark inside the tray icon.

``headwaters/features/tray/icon.py`` draws the icon at run time: a coloured disc with a white
droplet in it. These tests are about the droplet -- that it is actually drawn, that it does not
swallow the status colour the tray tests read at ``CENTRE_PROBE``, that the Private Mode lock is
still legible over it, and that the mark survives a 16x16 notification area.

They live in their own file, apart from ``test_hdw504_tray.py``, so work on the tray app does not
collide with work on the icon.
"""

from __future__ import annotations

import pytest
from PIL import Image

from headwaters.features.tray.icon import (
    AMBER_RGB,
    CENTRE_PROBE,
    GREEN_RGB,
    LOCK_PROBE,
    PURPLE_RGB,
    RED_RGB,
    SIZE,
    render,
)

STATES = {
    "green": GREEN_RGB,
    "amber": AMBER_RGB,
    "red": RED_RGB,
}

# Pixels that must stay white: two in the droplet's solid crown, two on its outline, one on the
# floor of its belly. Chosen well inside the strokes, not on an antialiased edge.
MARK_WHITE_POINTS = ((32, 20), (30, 23), (21, 40), (43, 40), (32, 49))

# Pixels that must stay the status colour: the strip of disc left of the mark, and -- the point of
# the design -- the water inside the droplet, so green/amber/red still show through the icon.
MARK_STATUS_POINTS = ((10, 32), (32, 34), (30, 38))


@pytest.mark.parametrize(("color", "expected_rgb"), sorted(STATES.items()))
def test_the_centre_probe_is_still_the_colour_of_the_state(
    color: str, expected_rgb: tuple[int, int, int]
) -> None:
    """The contract: the middle of the disc stays status-coloured, mark or no mark."""
    image = render(color).convert("RGBA")
    assert image.getpixel(CENTRE_PROBE)[:3] == expected_rgb


@pytest.mark.parametrize(("color", "expected_rgb"), sorted(STATES.items()))
def test_the_droplet_is_drawn_in_white_in_the_middle_of_the_disc(
    color: str, expected_rgb: tuple[int, int, int]
) -> None:
    image = render(color).convert("RGBA")
    for point in MARK_WHITE_POINTS:
        red, green, blue = image.getpixel(point)[:3]
        assert (red, green, blue) == (255, 255, 255), f"{point} should be solid white on {color}"


@pytest.mark.parametrize(("color", "expected_rgb"), sorted(STATES.items()))
def test_the_status_colour_still_shows_beside_and_through_the_mark(
    color: str, expected_rgb: tuple[int, int, int]
) -> None:
    image = render(color).convert("RGBA")
    for point in MARK_STATUS_POINTS:
        assert image.getpixel(point)[:3] == expected_rgb, f"{point} should be the disc colour"


@pytest.mark.parametrize(("color", "expected_rgb"), sorted(STATES.items()))
def test_the_private_mode_lock_still_renders_purple_over_the_mark(
    color: str, expected_rgb: tuple[int, int, int]
) -> None:
    plain = render(color).convert("RGBA")
    locked = render(color, private=True).convert("RGBA")
    assert plain.getpixel(LOCK_PROBE)[:3] == expected_rgb  # no lock: clear of the droplet
    assert locked.getpixel(LOCK_PROBE)[:3] == PURPLE_RGB  # lock body, drawn over the mark
    # The mark must not hide the lock, and the lock must not hide the status colour.
    assert locked.getpixel(CENTRE_PROBE)[:3] == expected_rgb
    for point in MARK_WHITE_POINTS[:2]:
        assert locked.getpixel(point)[:3] == (255, 255, 255)


@pytest.mark.parametrize(("color", "expected_rgb"), sorted(STATES.items()))
@pytest.mark.parametrize("private", [False, True])
def test_every_state_renders_at_size_as_rgba_with_transparent_corners(
    color: str, expected_rgb: tuple[int, int, int], private: bool
) -> None:
    image = render(color, private=private)
    assert isinstance(image, Image.Image)
    assert image.mode == "RGBA"
    assert image.size == (SIZE, SIZE)
    for corner in ((0, 0), (SIZE - 1, 0), (0, SIZE - 1), (SIZE - 1, SIZE - 1)):
        assert image.getpixel(corner)[3] == 0, f"{corner} should be outside the disc"
    # And the disc itself is drawn: a pixel inside it is opaque.
    assert image.getpixel((6, SIZE // 2))[3] == 255


@pytest.mark.parametrize("color", sorted(STATES))
def test_the_mark_survives_a_sixteen_pixel_notification_area(color: str) -> None:
    small = render(color).convert("RGBA").resize((16, 16), Image.LANCZOS)
    light = [
        (x, y)
        for y in range(16)
        for x in range(16)
        if all(channel > 190 for channel in small.getpixel((x, y))[:3])
    ]
    # The size a Windows notification area actually uses: the droplet has to stay a real, chunky
    # shape there, not blend away into the disc. It is drawn about 40 light pixels at this size.
    assert len(light) >= 20
    assert min(y for _, y in light) <= 3  # the apex still reads as a point at the top
    assert max(y for _, y in light) >= 12  # and the belly still reaches the bottom
