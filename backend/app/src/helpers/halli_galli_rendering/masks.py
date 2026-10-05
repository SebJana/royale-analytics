"""Mask geometry, bounded coordinate caches and fruit-contact bookkeeping."""

import math
import random
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageChops

from ..halli_galli_card import (
    BACKGROUND_FRUIT_CONTACT_LIMIT,
    BACKGROUND_FRUIT_CONTACT_WIDTH,
    CARD_HEIGHT,
    CARD_WIDTH,
)
from .models import (
    FruitImagePosition,
)


def fruit_interior_mask(foreground: Image.Image) -> Image.Image:
    """Use the fruit alpha for clipped noise and nearby background spacing."""
    return foreground.getchannel("A")


def expand_mask(mask: Image.Image, width: int) -> Image.Image:
    """Exact square maximum filter using native image shifts and maxima.

    Each pass doubles the covered range; padding prevents wrapped pixels from
    crossing an image edge. This avoids the large rank-filter kernel cost.
    """
    radius = width // 2
    expanded = Image.new("L", (mask.width + 2 * radius, mask.height + 2 * radius))
    expanded.paste(mask, (radius, radius))
    for axis in (0, 1):
        # Square dilation is separable: expand horizontal support, then vertical
        # support. Each step operates on the previous result, not the source.
        remaining = radius
        step = 1
        while remaining:
            distance = min(step, remaining)
            dx, dy = (distance, 0) if axis == 0 else (0, distance)
            expanded = ImageChops.lighter(
                expanded,
                ImageChops.lighter(
                    ImageChops.offset(expanded, dx, dy),
                    ImageChops.offset(expanded, -dx, -dy),
                ),
            )
            remaining -= distance
            step *= 2
    return expanded.crop((radius, radius, radius + mask.width, radius + mask.height))


@dataclass
class BackgroundFruitContacts:
    """Per-pixel fruit memberships and per-fruit accepted cluster counts."""

    labels: bytes
    limits: list[int]
    counts: list[int]


def create_background_fruit_contacts(
    foreground: Image.Image, positions: list[FruitImagePosition]
) -> BackgroundFruitContacts:
    """Label each fruit's nearby pixels without drawing a visible outline."""
    labels = bytearray(CARD_WIDTH * CARD_HEIGHT)
    alpha = fruit_interior_mask(foreground)
    padding = BACKGROUND_FRUIT_CONTACT_WIDTH // 2
    for fruit_index, position in enumerate(positions):
        left = max(0, math.floor(position.x * CARD_WIDTH) - padding)
        top = max(0, math.floor(position.y * CARD_HEIGHT) - padding)
        right = min(
            CARD_WIDTH, math.ceil((position.x + position.width) * CARD_WIDTH) + padding
        )
        bottom = min(
            CARD_HEIGHT,
            math.ceil((position.y + position.height) * CARD_HEIGHT) + padding,
        )
        nearby = expand_mask(
            alpha.crop((left, top, right, bottom)), BACKGROUND_FRUIT_CONTACT_WIDTH
        )
        for index, opacity in enumerate(nearby.getdata()):
            if opacity:
                card_index = (
                    (top + index // nearby.width) * CARD_WIDTH
                    + left
                    + index % nearby.width
                )
                labels[card_index] |= 1 << fruit_index
    return BackgroundFruitContacts(
        labels=bytes(labels),
        limits=[random.randint(*BACKGROUND_FRUIT_CONTACT_LIMIT) for _ in positions],
        counts=[0] * len(positions),
    )


def component_pixel_indices(
    mask: Image.Image, left: int, top: int, card_width: int = CARD_WIDTH
) -> list[int]:
    """Map painted mask pixels to card indices at the chosen position."""
    width = mask.width
    offsets = get_component_offsets(width, mask.tobytes())
    origin = top * card_width + left
    return [origin + y * card_width + x for x, y in offsets]


@lru_cache(maxsize=128)
def get_component_offsets(width: int, mask_bytes: bytes) -> tuple[tuple[int, int], ...]:
    """Reuse a decoy's painted coordinates across placement retries."""
    return tuple(
        (index % width, index // width)
        for index, value in enumerate(mask_bytes)
        if value
    )


def component_placement_is_valid(
    indices: list[int], occupied: bytearray, exclusion: bytes
) -> bool:
    """Keep all blob pixels outside the fruit clearance area."""
    if any(exclusion[index] for index in indices):
        return False
    # At most 1/20 (5%) of a candidate may overlap other background components.
    # This leaves most of its shape intact and avoids merging several decoys
    # into one large patch. The fruit exclusion above allows no overlap at all.
    return sum(occupied[index] for index in indices) <= len(indices) // 20


@lru_cache(maxsize=1)
def get_exclusion_mask(exclusion: bytes, size: tuple[int, int]) -> Image.Image:
    """Reuse one clearance image throughout a card's placement retries."""
    return Image.frombytes("L", size, exclusion)
