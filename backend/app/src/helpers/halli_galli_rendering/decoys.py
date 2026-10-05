"""Contrasting fruit-colored background distractions and placement retries."""

import random

from PIL import Image, ImageChops, ImageDraw

from .assets import (
    get_fruit_colors,
)
from .colors import (
    count_foreground_hues,
    create_component_texture,
    get_contrasting_fruits,
    get_decoy_palette,
)
from ..halli_galli_card import (
    BACKGROUND_DECOY_COUNT,
    BACKGROUND_DECOY_FALLBACK_ATTEMPTS,
    BACKGROUND_DECOY_FALLBACK_SIZE,
    BACKGROUND_DECOY_PLACEMENT_ATTEMPTS,
    BACKGROUND_DECOY_SHADES,
    BACKGROUND_DECOY_SIZE,
    ColorPalette,
)
from .layout import (
    clamp,
)
from .masks import (
    component_pixel_indices,
    component_placement_is_valid,
    get_exclusion_mask,
)


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


def draw_component_artifacts(
    texture: Image.Image, points: list[tuple[int, int]], accent_palette: ColorPalette
) -> None:
    """Add small source-colored marks to the painted part of a blob."""
    detail = ImageDraw.Draw(texture)
    # 65-110 specks, each 1-3px across, give the decoy a fruit-like surface.
    # Sample only inside the mask so most detail reaches the painted shape.
    for x, y in random.choices(points, k=random.randint(65, 110)):
        detail.rectangle(
            (x, y, x + random.randint(0, 2), y + random.randint(0, 2)),
            fill=random.choice(accent_palette),
        )
    # Four short, 1-2px wide scratches add detail without replacing the surface
    # with dense noise. Their endpoints stay within 5px of the sampled position.
    for x, y in random.choices(points, k=4):
        detail.line(
            (x, y, x + random.randint(-5, 5), y + random.randint(-5, 5)),
            fill=random.choice(accent_palette),
            width=random.randint(1, 2),
        )


def paint_component_texture(
    background: Image.Image,
    texture: Image.Image,
    indices: list[int],
    left: int,
    top: int,
    occupied: bytearray,
    hue_counts: list[int],
) -> None:
    """Paint accepted pixels and count their final colors for balancing."""
    card_width = background.width
    mask = Image.new("L", texture.size)
    mask_pixels = mask.load()
    # Only newly occupied pixels contribute, preserving the overlap rule.
    for index in indices:
        if occupied[index]:
            continue
        occupied[index] = 1
        mask_pixels[index % card_width - left, index // card_width - top] = 255
    background.paste(texture, (left, top), mask)
    painted = texture.convert("RGBA")
    painted.putalpha(mask)
    for hue_bin, count in enumerate(count_foreground_hues(painted)):
        hue_counts[hue_bin] += count


def place_colored_component(
    background: Image.Image,
    occupied: bytearray,
    exclusion: bytes,
    palette: ColorPalette,
    accent_palette: ColorPalette,
    brightness: float,
    mask: Image.Image,
    hue_counts: list[int],
) -> int:
    """Try one position, then texture and paint the accepted blob."""
    # Keep the mask viewport a couple of pixels inside the image. The final
    # card alpha handles the rounded corners without drawing a separate border.
    left = random.randint(2, background.width - mask.width - 3)
    top = random.randint(2, background.height - mask.height - 3)
    clearance = get_exclusion_mask(exclusion, background.size)
    if ImageChops.multiply(
        mask, clearance.crop((left, top, left + mask.width, top + mask.height))
    ).getbbox():
        return 0
    indices = component_pixel_indices(mask, left, top, background.width)
    if not component_placement_is_valid(indices, occupied, exclusion):
        return 0

    # Texture work happens only after placement succeeds. Rejected positions
    # do not need a rendered surface or spend extra random color selections.
    texture = create_component_texture(mask.size, palette, brightness)
    points = [
        (index % background.width - left, index // background.width - top)
        for index in indices
    ]
    draw_component_artifacts(texture, points, accent_palette)
    paint_component_texture(
        background, texture, indices, left, top, occupied, hue_counts
    )
    return len(indices)


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


def try_place_decoy(
    background: Image.Image,
    occupied: bytearray,
    exclusion: bytes,
    source: str,
    brightness: float,
    mask: Image.Image,
    hue_counts: list[int],
    attempts: int,
) -> bool:
    """Retry positions for one fixed shape and palette without rerolling them."""
    colors = get_decoy_palette(source)
    accents = get_fruit_colors(source)
    for _ in range(attempts):
        if place_colored_component(
            background,
            occupied,
            exclusion,
            colors,
            accents,
            brightness,
            mask,
            hue_counts,
        ):
            return True
    return False


def add_decoy_components(
    background: Image.Image,
    occupied: bytearray,
    fruit: str,
    exclusion: bytes,
    hue_counts: list[int],
    amount: int,
) -> None:
    """Place the big distractions before spending pixels on histogram balance."""
    for source, brightness in choose_decoy_sources(fruit, amount):
        mask = create_decoy_mask(fruit)
        if try_place_decoy(
            background,
            occupied,
            exclusion,
            source,
            brightness,
            mask,
            hue_counts,
            BACKGROUND_DECOY_PLACEMENT_ATTEMPTS,
        ):
            continue
        # Retry this palette rather than replacing rejected rivals with the
        # actual fruit color. Keep the same shape, shade, and fruit clearance.
        size = BACKGROUND_DECOY_FALLBACK_SIZE
        mask = mask.resize((size, size), Image.Resampling.NEAREST)
        try_place_decoy(
            background,
            occupied,
            exclusion,
            source,
            brightness,
            mask,
            hue_counts,
            BACKGROUND_DECOY_FALLBACK_ATTEMPTS,
        )
