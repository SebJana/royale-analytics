"""Contrasting fruit-colored background distractions and placement retries."""

import random

import numpy as np
from PIL import Image, ImageDraw

from .colors import (
    count_painted_hues,
    create_component_texture,
    get_contrasting_fruits,
    get_fruit_color_array,
    get_decoy_palette,
)
from .constants import (
    BACKGROUND_DECOY_COUNT,
    BACKGROUND_DECOY_FALLBACK_ATTEMPTS,
    BACKGROUND_DECOY_FALLBACK_SIZE,
    BACKGROUND_DECOY_PLACEMENT_ATTEMPTS,
    BACKGROUND_DECOY_SHADES,
    BACKGROUND_DECOY_SIZE,
    CARD_HEIGHT,
    CARD_WIDTH,
    ColorPalette,
)
from .layout import (
    clamp,
)

# Random positions are tried this often before the free positions are listed.
# Most decoys fit within a few tries; crowded cards then avoid hundreds more.
DECOY_RANDOM_ATTEMPTS = 10


def draw_blob_lobe(
    draw: ImageDraw.ImageDraw, x: int, y: int, radius_x: int, radius_y: int
) -> None:
    """Fill one lobe shared by both connected blob shapes."""
    draw.ellipse((x - radius_x, y - radius_y, x + radius_x, y + radius_y), fill=255)


def draw_compact_decoy(draw: ImageDraw.ImageDraw, size: int) -> None:
    """Build a wide patch with overlapping lobes around one center."""
    # Coordinates are tuned for the 100px mask. Centering the 40px core in the
    # middle 30px leaves room for bulges in every direction without a crescent.
    center_x, center_y = random.randint(35, 65), random.randint(35, 65)
    draw_blob_lobe(draw, center_x, center_y, 20, 20)
    # Five to eight lobes with 14-24px horizontal and 11-20px vertical radii
    # make the patch broad and uneven. Center clamps limit clipping at the edges.
    for _ in range(random.randint(5, 8)):
        x = int(clamp(center_x + random.randint(-22, 22), 17, size - 18))
        y = int(clamp(center_y + random.randint(-18, 18), 17, size - 18))
        radius_x = random.randint(14, 24)
        radius_y = random.randint(11, 20)
        # Connect every lobe to the center so it stays one component.
        draw.line((center_x, center_y, x, y), fill=255, width=17)
        draw_blob_lobe(draw, x, y, radius_x, radius_y)


def draw_elongated_decoy(draw: ImageDraw.ImageDraw, size: int) -> None:
    """Join broad lobes and add an uneven side branch."""
    # Three staggered centers span most of the 100px mask. The middle lobe has
    # more vertical freedom so the cluster bends rather than forming an oval.
    points = [
        (random.randint(21, 31), random.randint(34, 59)),
        (random.randint(42, 56), random.randint(28, 66)),
        (random.randint(68, 79), random.randint(34, 59)),
    ]
    # A 25-32px connector and broad 15-23/14-21px lobes keep the shape blobby
    # and connected instead of producing thin strings or separate round fruits.
    draw.line(points, fill=255, width=random.randint(25, 32), joint="curve")
    for x, y in points:
        radius_x = random.randint(15, 23)
        radius_y = random.randint(14, 21)
        draw_blob_lobe(draw, x, y, radius_x, radius_y)
    x, y = random.choice(points)
    # Offset a 12px side lobe by 17-27px vertically. A thick connector keeps
    # it attached and the branch breaks the round silhouette of the main cluster.
    tip_x = int(clamp(x + random.randint(-19, 19), 13, size - 14))
    tip_y = int(
        clamp(y + random.choice((-1, 1)) * random.randint(17, 27), 13, size - 14)
    )
    draw.line((x, y, tip_x, tip_y), fill=255, width=random.randint(15, 21))
    draw_blob_lobe(draw, tip_x, tip_y, 12, 12)


def create_decoy_mask(fruit: str) -> Image.Image:
    """Make an irregular connected blob without recognizable fruit details."""
    size = BACKGROUND_DECOY_SIZE
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    # Mix both shapes independently of the actual fruit. Otherwise the decoy
    # silhouette itself tells a classifier whether this is a banana card.
    shape_drawer = random.choice((draw_compact_decoy, draw_elongated_decoy))
    shape_drawer(draw, size)
    # Half the masks turn by 90 degrees so elongated clusters have both axes.
    if random.random() < 0.5:
        mask = mask.transpose(Image.Transpose.ROTATE_90)
    return mask


def create_decoy_texture(
    mask: np.ndarray,
    palette: ColorPalette,
    accents: np.ndarray,
    brightness: float,
    rng: np.random.Generator,
) -> np.ndarray:
    """Shade a blob surface and add small source-colored marks inside it."""
    height, width = mask.shape
    texture = create_component_texture((width, height), palette, brightness)
    # Four short, 1-2px wide scratches add detail without replacing the surface
    # with dense noise. Their endpoints stay within 5px of a painted position.
    inside = np.argwhere(mask)
    detail = ImageDraw.Draw(texture)
    for y, x in inside[rng.integers(len(inside), size=4)]:
        detail.line(
            (
                int(x),
                int(y),
                int(x) + random.randint(-5, 5),
                int(y) + random.randint(-5, 5),
            ),
            fill=tuple(int(c) for c in accents[rng.integers(len(accents))]),
            width=random.randint(1, 2),
        )
    pixels = np.array(texture)
    # 65-110 specks, each 1-3px across, give the decoy a fruit-like surface.
    # Sample only inside the mask so most detail reaches the painted shape.
    count = random.randint(65, 110)
    origins = inside[rng.integers(len(inside), size=count)]
    sizes = rng.integers(1, 4, size=(count, 2))
    colors = accents[rng.integers(len(accents), size=count)]
    for dy in range(3):
        for dx in range(3):
            keep = (sizes[:, 0] > dy) & (sizes[:, 1] > dx)
            ys = np.minimum(origins[keep, 0] + dy, height - 1)
            xs = np.minimum(origins[keep, 1] + dx, width - 1)
            pixels[ys, xs] = colors[keep]
    return pixels


def find_decoy_position(
    mask: np.ndarray,
    occupied: np.ndarray,
    clearance: np.ndarray,
    attempts: int,
) -> tuple[int, int] | None:
    """Pick a top-left that keeps the blob off the fruit clearance.

    Random positions first; once those fail, every position whose whole box
    is clear is listed with one summed-area pass, and half the remaining
    attempts draw from that list.

    Args:
        mask (np.ndarray): Boolean blob shape.
        occupied (np.ndarray): Card pixels already painted.
        clearance (np.ndarray): Card pixels reserved around real fruit.
        attempts (int): Placement attempts for this shape.

    Returns:
        tuple[int, int] | None: Top and left, or None if nothing fits.
    """
    height, width = mask.shape
    painted = int(mask.sum())
    free = None
    for attempt in range(attempts):
        if attempt == DECOY_RANDOM_ATTEMPTS:
            table = np.pad(clearance.astype(np.int32), ((1, 0), (1, 0)))
            table = table.cumsum(0).cumsum(1)
            sums = (
                table[height:, width:]
                - table[:-height, width:]
                - table[height:, :-width]
                + table[:-height, :-width]
            )[2 : CARD_HEIGHT - height - 2, 2 : CARD_WIDTH - width - 2]
            free = np.argwhere(sums == 0) + 2
        if free is not None and len(free) and attempt < attempts // 2:
            top, left = (int(value) for value in free[random.randrange(len(free))])
        else:
            # Keep the mask viewport a couple of pixels inside the image. The
            # final card alpha handles the rounded corners.
            left = random.randint(2, CARD_WIDTH - width - 3)
            top = random.randint(2, CARD_HEIGHT - height - 3)
        window = (slice(top, top + height), slice(left, left + width))
        if np.any(clearance[window][mask]):
            continue
        # At most 1/20 (5%) of a candidate may overlap other background
        # components. This leaves most of its shape intact and avoids merging
        # several decoys into one large patch.
        if np.count_nonzero(occupied[window][mask]) > painted // 20:
            continue
        return top, left
    return None


def choose_decoy_sources(fruit: str, amount: int) -> list[tuple[str, float]]:
    """Choose zero or more contrasting decoys, independently of fruit amount."""
    rivals = get_contrasting_fruits(fruit)
    # Do not offset by amount: every card can have zero decoys, and decoy count
    # must not act as an indirect clue to how many real fruits it contains.
    count = random.randint(*BACKGROUND_DECOY_COUNT)
    sources = random.sample(rivals, min(count, len(rivals)))
    sources.extend(random.choice(rivals) for _ in range(count - len(sources)))
    # Mix placement order so rival palettes do not have fixed roles when crowded
    # cards run out of space. Each accepted blob gets its own shade.
    random.shuffle(sources)
    shades = [random.choice(BACKGROUND_DECOY_SHADES) for _ in range(count)]
    return [(source, brightness) for source, brightness in zip(sources, shades)]


def add_decoy_components(
    background: np.ndarray,
    occupied: np.ndarray,
    fruit: str,
    clearance: np.ndarray,
    hue_counts: np.ndarray,
    amount: int,
    rng: np.random.Generator,
) -> None:
    """Place the big distractions before spending pixels on histogram balance.

    Args:
        background (np.ndarray): Card RGB pixels, painted in place.
        occupied (np.ndarray): Card pixels already painted, updated in place.
        fruit (str): Actual fruit, whose palette decoys never use.
        clearance (np.ndarray): Card pixels reserved around real fruit.
        hue_counts (np.ndarray): Vivid pixels per hue bin, updated in place.
        amount (int): Actual fruit count; decoy counts ignore it.
        rng (np.random.Generator): This card's NumPy random stream.
    """
    for source, brightness in choose_decoy_sources(fruit, amount):
        mask_image = create_decoy_mask(fruit)
        # Retry this palette rather than replacing rejected rivals with the
        # actual fruit color. Keep the same shape, shade, and fruit clearance.
        for size, attempts in (
            (None, BACKGROUND_DECOY_PLACEMENT_ATTEMPTS),
            (BACKGROUND_DECOY_FALLBACK_SIZE, BACKGROUND_DECOY_FALLBACK_ATTEMPTS),
        ):
            if size is not None:
                mask_image = mask_image.resize((size, size), Image.Resampling.NEAREST)
            mask = np.asarray(mask_image) > 0
            position = find_decoy_position(mask, occupied, clearance, attempts)
            if position is None:
                continue
            top, left = position
            window = (
                slice(top, top + mask.shape[0]),
                slice(left, left + mask.shape[1]),
            )
            # Texture work happens only after placement succeeds.
            texture = create_decoy_texture(
                mask,
                get_decoy_palette(source),
                get_fruit_color_array(source),
                brightness,
                rng,
            )
            # Only newly occupied pixels contribute, preserving the overlap rule.
            paint = mask & ~occupied[window]
            background[window][paint] = texture[paint]
            occupied[window] |= paint
            hue_counts += count_painted_hues(texture, paint)
            break
