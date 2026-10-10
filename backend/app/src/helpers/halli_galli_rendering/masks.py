"""Mask geometry and fruit-contact bookkeeping on NumPy arrays."""

import math
import random
from dataclasses import dataclass

import numpy as np

from .constants import (
    BACKGROUND_FRUIT_CONTACT_LIMIT,
    BACKGROUND_FRUIT_CONTACT_WIDTH,
    CARD_HEIGHT,
    CARD_WIDTH,
)
from .models import (
    FruitImagePosition,
)

CARD_SHAPE = (CARD_HEIGHT, CARD_WIDTH)


def fruit_pixel_box(position: FruitImagePosition) -> tuple[int, int, int, int]:
    """Return a fruit's click box as whole card pixels, clipped to the card.

    Args:
        position (FruitImagePosition): Normalized painted bounds of one fruit.

    Returns:
        tuple[int, int, int, int]: Left, top, right and bottom (exclusive).
    """
    left = max(0, math.floor(position.x * CARD_WIDTH))
    top = max(0, math.floor(position.y * CARD_HEIGHT))
    right = min(CARD_WIDTH, math.ceil((position.x + position.width) * CARD_WIDTH))
    bottom = min(CARD_HEIGHT, math.ceil((position.y + position.height) * CARD_HEIGHT))
    return left, top, right, bottom


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """Exact square dilation within the mask's own bounds.

    A summed-area table counts the set pixels of every window in one pass, so
    the cost does not grow with the radius.

    Args:
        mask (np.ndarray): Boolean mask.
        radius (int): Pixels the mask grows in every direction.

    Returns:
        np.ndarray: Boolean mask of the same shape.
    """
    padded = np.pad(mask.astype(np.int32), radius + 1)
    table = padded.cumsum(0).cumsum(1)
    size = 2 * radius + 1
    window = (
        table[size:, size:]
        - table[:-size, size:]
        - table[size:, :-size]
        + table[:-size, :-size]
    )
    return window[: mask.shape[0], : mask.shape[1]] > 0


def fruit_clearance(painted: np.ndarray, width: int) -> np.ndarray:
    """Reserve ``width // 2`` pixels around painted fruit for big decoys only.

    The clearance only has to keep decoys away, not follow the fruit outline to
    the pixel, so it is dilated at half resolution. One extra coarse pixel keeps
    it at least as wide as the exact dilation.

    Args:
        painted (np.ndarray): Card-sized mask of painted fruit pixels.
        width (int): Full window width, as for a max filter.

    Returns:
        np.ndarray: Card-sized boolean clearance mask.
    """
    coarse = painted.reshape(CARD_HEIGHT // 2, 2, CARD_WIDTH // 2, 2).any(axis=(1, 3))
    expanded = dilate(coarse, width // 4 + 1)
    return expanded.repeat(2, axis=0).repeat(2, axis=1)


@dataclass
class BackgroundFruitContacts:
    """Each fruit's surroundings and how many clusters may touch them."""

    bands: list[np.ndarray]
    limits: list[int]


def create_background_fruit_contacts(
    painted: np.ndarray, positions: list[FruitImagePosition]
) -> BackgroundFruitContacts:
    """Mark each fruit's nearby pixels without drawing a visible outline.

    Args:
        painted (np.ndarray): Card-sized mask of painted fruit pixels.
        positions (list[FruitImagePosition]): Click boxes, one per fruit.

    Returns:
        BackgroundFruitContacts: One card-sized band and limit per fruit.
    """
    reach = BACKGROUND_FRUIT_CONTACT_WIDTH // 2
    bands = []
    for position in positions:
        left, top, right, bottom = fruit_pixel_box(position)
        # Dilate only this fruit's pixels, inside its box plus the reach.
        window = (
            slice(max(0, top - reach), min(CARD_HEIGHT, bottom + reach)),
            slice(max(0, left - reach), min(CARD_WIDTH, right + reach)),
        )
        own = np.zeros(CARD_SHAPE, dtype=bool)
        own[top:bottom, left:right] = painted[top:bottom, left:right]
        band = np.zeros(CARD_SHAPE, dtype=bool)
        band[window] = dilate(own[window], reach)
        bands.append(band)
    return BackgroundFruitContacts(
        bands=bands,
        limits=[random.randint(*BACKGROUND_FRUIT_CONTACT_LIMIT) for _ in positions],
    )
