"""Central Fruit Buzz card configuration and generation API.

Tune card appearance in ``fruit_buzz_rendering.constants``. ``create_card`` orchestrates
fruit selection/layout, surface artifacts, balanced background colors, and
card-wide overlays; ``pick_random_card`` selects a supported fruit/count.
Detailed drawing operations and metadata models live in ``fruit_buzz_rendering``.

Generation flow, in execution order:

1. Request and source artwork (``create_card`` / ``assets``): validate the fruit
   and count, select one SVG variant per real fruit, and read its visible hull.
   Cached source artwork, hulls, and palettes avoid repeating asset work.

2. Placement (``layout``): sample each fruit's scale, horizontal flip, rotation,
   and color variation once. Shift the count's base formation as a whole, then
   jitter each center independently. Check transformed painted bounds against
   the card edges, rounded corners, and spacing from already placed fruits.
   Retry individual centers and then whole layouts within fixed limits; if
   exhausted, use the base formation with the already selected styles.

3. Artwork augmentation (``svg``): scale each fruit's cached raster (rendered
   once per SVG), adjust its hue, saturation, and lightness, darken bright
   colored fills for contrast, then flip and rotate it. Composite the transparent foreground. Derive private click boxes
   from the same transformed visible hulls used for placement.

4. Fruit surface artifacts (``artifacts``): scatter fine specks and short
   streaks, then add 1-2 larger textured discoloration patches in contrasting
   fruit colors. Patch area is bounded per fruit; translucency leaves underlying
   detail visible. Clip surface marks to painted fruit so silhouettes survive.

5. Paper background (``background``): tint a shared, flipped grayscale paper
   texture. Measure vivid hue bins in the augmented foreground and reserve its
   painted pixels before placing background distractions.

6. Large blobs (``decoys``): sample 0-5 connected, shaded fruit-like blobs
   independently of the real count. Use rival palettes, excluding the actual
   fruit's palette; banana/orange use only strawberry/grape colors. Reserve
   clearance around real fruit and reject overlaps. Retry crowded placements
   with a smaller version of the same shape, palette, and shade, then skip it
   if necessary. Add accepted blob colors to the hue counts.

7. Balancing clusters and fragments (``background`` / ``masks``): subtract
   foreground and large-blob colors from shared, randomized hue targets, then
   fill deficits in shuffled hue order with connected brush walks, stamped
   from a self-refreshing shape library at random positions. Accept or reject
   whole clusters rather than thinning them into loose pixels. Limit the clusters
   touching each real fruit to its sampled contact limit. Stop at the minimum cluster size or
   retry budget, leaving small residual deficits instead of tiny finishing
   fragments. Histogram balance is approximate, not identical.

8. Final overlays and output (``compose_card`` / ``artifacts``): composite fruit
   over paper, then add neutral hollow rings and thick squiggly strokes across
   both. Reject marks that exceed card-wide or per-fruit coverage limits. Apply
   the rounded card alpha and encode PNG bytes in memory. Return the image and
   private fruit/count/click-box metadata.
"""

import random
from io import BytesIO

import numpy as np
from PIL import Image

from .fruit_buzz_rendering.artifacts import add_card_artifacts, add_fruit_artifacts
from .fruit_buzz_rendering.assets import get_visible_svg_hull, load_random_fruit_svg
from .fruit_buzz_rendering.background import create_noisy_background
from .fruit_buzz_rendering.constants import (
    AVAILABLE_FRUITS,
    CARD_PNG_COMPRESS_LEVEL,
    FRUIT_POSITIONS,
)
from .fruit_buzz_rendering.layout import create_fruit_placements
from .fruit_buzz_rendering.models import FruitImagePosition, FruitBuzzCard
from .fruit_buzz_rendering.svg import get_card_mask, render_fruit_foreground

# The card pool and the game import the supported cards from here.
__all__ = ["AVAILABLE_FRUITS", "FRUIT_POSITIONS", "create_card", "pick_random_card"]


# High-level generation flow; drawing details remain in the stage modules.


def compose_card(
    background: Image.Image,
    foreground: Image.Image,
    fruit_positions: list[FruitImagePosition],
) -> bytes:
    """Layer the fruit foreground over the generated background and encode PNG.

    Args:
        background (Image.Image): Tinted card background.
        foreground (Image.Image): Rasterized fruit icons.
        fruit_positions (list[FruitImagePosition]): Rendered fruit bounds,
            which cap the overlay coverage per fruit.

    Returns:
        bytes: Complete flattened PNG for caching and later encryption.
    """

    card = add_card_artifacts(
        Image.alpha_composite(background, foreground), foreground, fruit_positions
    )
    # Set the cached outer alpha directly so corner rounding is consistent
    # across both layers and no painted outline is needed around the PNG.
    card.putalpha(get_card_mask())
    output = BytesIO()
    # Encode in memory for the game/cache. Only external scripts save files.
    card.save(output, format="PNG", compress_level=CARD_PNG_COMPRESS_LEVEL)
    return output.getvalue()


def create_fruit_foreground(
    fruit: str, amount: int
) -> tuple[Image.Image, list[FruitImagePosition]]:
    """Render the fruit layer and keep its final click boxes together.

    Args:
        fruit (str): Fruit type to draw.
        amount (int): Number of fruit icons, a key of FRUIT_POSITIONS.

    Returns:
        tuple[Image.Image, list[FruitImagePosition]]: The transparent fruit
            layer with its surface artifacts, and the click box of each fruit.
    """
    fruit_svgs = [load_random_fruit_svg(fruit) for _ in FRUIT_POSITIONS[amount]]
    visible_hulls = [get_visible_svg_hull(fruit_svg) for fruit_svg in fruit_svgs]
    placements = create_fruit_placements(FRUIT_POSITIONS[amount], amount, visible_hulls)
    # Derive click boxes from the same placements that are rendered. Artifacts
    # stay inside the painted alpha and do not change the target positions.
    fruit_positions = [
        placement.to_image_position(visible_hull)
        for visible_hull, placement in zip(visible_hulls, placements)
    ]
    foreground = render_fruit_foreground(fruit_svgs, placements)
    # Seeded from the standard RNG, so one generator decides the draws of a
    # card's stages instead of NumPy's global state.
    rng = np.random.Generator(np.random.PCG64(random.getrandbits(128)))
    return (
        add_fruit_artifacts(foreground, fruit_positions, fruit, rng),
        fruit_positions,
    )


def create_card(fruit: str, amount: int) -> FruitBuzzCard | None:
    """Create a PNG card containing ``amount`` of ``fruit``.

    Fruit SVGs are rasterized once, then transformed and layered into a PNG.

    Args:
        fruit (str): Fruit type to draw on the card.
        amount (int): Number of fruit icons to draw.

    Returns:
        FruitBuzzCard | None: Completed PNG and server-side hit boxes, or None
            when the requested fruit/count is unsupported.
    """
    # The stages are described in the module docstring. The card is sent as a
    # flattened PNG: JSON, drawing instructions or SVG would expose the fruit,
    # count, paths and boundaries that make automated classification cheap.
    # A multimodal LLM or a trained vision model can still count the fruit, so
    # this raises the cost of automation; it is not a security boundary.
    if fruit not in AVAILABLE_FRUITS:
        return None
    if amount not in FRUIT_POSITIONS:
        return None

    # Keep foreground, background and card-wide overlays in this order: the
    # background balances the hues the foreground leaves, the overlays cover
    # both. The same positions feed clearance checks and the click boxes.
    foreground, fruit_positions = create_fruit_foreground(fruit, amount)
    background = create_noisy_background(foreground, fruit, amount, fruit_positions)
    return FruitBuzzCard(
        image=compose_card(background, foreground, fruit_positions),
        fruit=fruit,
        amount=amount,
        fruit_positions=fruit_positions,
    )


def pick_random_card() -> tuple[str, int]:
    """Pick one random valid Fruit Buzz card.

    A fruit and supported count are selected independently.

    Returns:
        tuple[str, int]: The fruit type and amount of fruit on the card.
    """
    random_fruit = random.choice(AVAILABLE_FRUITS)
    random_amount = random.choice(tuple(FRUIT_POSITIONS))

    return random_fruit, random_amount
