"""Fruit style sampling and bounded, non-overlapping placement retries."""

import random

from .constants import (
    CARD_CORNER_RADIUS,
    CARD_HEIGHT,
    CARD_WIDTH,
    FORMATION_POSITION_SHIFT,
    FRUIT_GAP,
    FRUIT_LAYOUT_ATTEMPTS,
    FRUIT_PLACEMENT_ATTEMPTS,
    FRUIT_POSITION_JITTER,
    FRUIT_ROTATION_RANGES,
    FRUIT_SCALE_RANGES,
)
from .models import (
    FruitCreationPlacement,
    FruitImageBounds,
    FruitStyle,
)


def create_fruit_style(amount: int) -> FruitStyle:
    """Create subtle, independently testable visual variation for one fruit.

    Args:
        amount (int): Fruit count used to choose safe size and rotation ranges.

    Returns:
        FruitStyle: Scale, flip, rotation, and color changes for one icon.
    """

    # Amount-specific ranges keep sparse cards expressive without making dense
    # layouts impossible to place after rotation and collision checks.
    # A +/-0.03 hue turn and roughly 10% saturation/6% lightness variation keep
    # the fruit recognizable while avoiding an identical fill on every instance.
    return FruitStyle(
        scale=random.uniform(*FRUIT_SCALE_RANGES[amount]),
        is_horizontally_flipped=random.choice((True, False)),
        rotation_degrees=random.uniform(*FRUIT_ROTATION_RANGES[amount]),
        hue_shift=random.uniform(-0.03, 0.03),
        saturation_multiplier=random.uniform(0.90, 1.10),
        lightness_multiplier=random.uniform(0.94, 1.06),
    )


def create_formation_offset(amount: int) -> tuple[float, float]:
    """Create one shared x/y offset for the card's entire fruit formation.

    Args:
        amount (int): Fruit count used to choose the shift range.

    Returns:
        tuple[float, float]: Horizontal and vertical shifts in pixels.
    """

    max_x_shift, max_y_shift = FORMATION_POSITION_SHIFT[amount]
    return (
        random.uniform(-max_x_shift, max_x_shift),
        random.uniform(-max_y_shift, max_y_shift),
    )


def clamp(value: float, lower: float, upper: float) -> float:
    """Clamp ``value`` to an inclusive numeric interval.

    Args:
        value (float): Number being limited.
        lower (float): Smallest accepted value.
        upper (float): Largest accepted value.

    Returns:
        float: Value within the given limits.
    """

    return max(lower, min(value, upper))


def image_position_is_valid(
    candidate: FruitImageBounds,
    placed_positions: list[FruitImageBounds],
) -> bool:
    """Check that a painted bounding box stays on-card and clear of others.

    Args:
        candidate (FruitImageBounds): Proposed fruit box after transforms.
        placed_positions (list[FruitImageBounds]): Boxes already accepted.

    Returns:
        bool: Whether the new box fits without touching another fruit.
    """

    horizontal_margin = CARD_CORNER_RADIUS / CARD_WIDTH
    vertical_margin = CARD_CORNER_RADIUS / CARD_HEIGHT
    horizontal_gap = FRUIT_GAP / CARD_WIDTH
    vertical_gap = FRUIT_GAP / CARD_HEIGHT

    if not (
        candidate.x >= horizontal_margin
        and candidate.y >= vertical_margin
        and candidate.x + candidate.width <= 1 - horizontal_margin
        and candidate.y + candidate.height <= 1 - vertical_margin
    ):
        return False

    # The returned painted boxes are the same boxes used here. A separating-axis
    # check is therefore enough to reject boxes that touch or overlap.
    return all(
        candidate.x + candidate.width + horizontal_gap <= position.x
        or position.x + position.width + horizontal_gap <= candidate.x
        or candidate.y + candidate.height + vertical_gap <= position.y
        or position.y + position.height + vertical_gap <= candidate.y
        for position in placed_positions
    )


def create_standard_fruit_placements(
    relative_positions: list[tuple[float, float]],
    styles: list[FruitStyle],
) -> list[FruitCreationPlacement]:
    """Return the original unshifted formation when random placement is exhausted.

    Args:
        relative_positions (list[tuple[float, float]]): Base fruit centers.
        styles (list[FruitStyle]): Artwork changes selected for these fruits.

    Returns:
        list[FruitCreationPlacement]: Original centers with the same styles.
    """

    # The base formations were chosen to be readable before any variation. Keep
    # the already-selected styles so a fallback does not unexpectedly reroll art.
    return [
        FruitCreationPlacement(
            relative_x=relative_x,
            relative_y=relative_y,
            style=style,
        )
        for (relative_x, relative_y), style in zip(relative_positions, styles)
    ]


def create_fruit_placements(
    relative_positions: list[tuple[float, float]],
    amount: int,
    visible_hulls: list[tuple[tuple[float, float], ...]],
) -> list[FruitCreationPlacement]:
    """Jitter fruits while enforcing their final painted bounds and spacing.

    Args:
        relative_positions (list[tuple[float, float]]): Base fruit centers.
        amount (int): Fruit count used for position and style limits.
        visible_hulls (list[tuple[tuple[float, float], ...]]): Painted outline
            for each selected source SVG.

    Returns:
        list[FruitCreationPlacement]: Non-overlapping randomized positions, or
            the original formation when the retry limit is exhausted.
    """

    if len(relative_positions) != len(visible_hulls):
        raise ValueError("Each fruit position requires one visible SVG hull")

    # Keep visual identity fixed while trying alternate positions for it.
    styles = [create_fruit_style(amount) for _ in relative_positions]

    # A valid early jitter can still leave too little room for a later fruit.
    # Retry the entire layout rather than weakening the no-overlap constraint.
    for _ in range(FRUIT_LAYOUT_ATTEMPTS):
        placements = []
        image_positions = []
        # This moves the die-like formation as one unit before each fruit gets
        # its own jitter, avoiding a recognizable fixed formation origin.
        formation_x, formation_y = create_formation_offset(amount)
        max_x_jitter, max_y_jitter = FRUIT_POSITION_JITTER[amount]

        for (relative_x, relative_y), style, visible_hull in zip(
            relative_positions, styles, visible_hulls
        ):
            base_x = CARD_WIDTH * relative_x + formation_x
            base_y = CARD_HEIGHT * relative_y + formation_y

            for _ in range(FRUIT_PLACEMENT_ATTEMPTS):
                center_x = base_x + random.uniform(-max_x_jitter, max_x_jitter)
                center_y = base_y + random.uniform(-max_y_jitter, max_y_jitter)
                candidate = FruitCreationPlacement(
                    relative_x=center_x / CARD_WIDTH,
                    relative_y=center_y / CARD_HEIGHT,
                    style=style,
                )
                candidate_position = candidate.to_image_bounds(visible_hull)

                if image_position_is_valid(candidate_position, image_positions):
                    placements.append(candidate)
                    image_positions.append(candidate_position)
                    break
            else:
                break

        if len(placements) == len(relative_positions):
            return placements

    # Random variation is optional; retain a predictable base formation if an
    # unusually constrained randomized layout exhausts its attempts.
    return create_standard_fruit_placements(relative_positions, styles)
