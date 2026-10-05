"""Paper texture and whole-cluster filling of shared hue deficits."""

import random

import numpy as np
from PIL import Image, ImageChops, ImageDraw

from .colors import (
    count_foreground_hues,
    get_background_bin_color,
    get_background_bin_palettes,
    get_background_hue_bins,
    get_background_palette,
    get_color_hue_bin,
)
from ..halli_galli_card import (
    BACKGROUND_BLOB_RADIUS,
    BACKGROUND_BLOB_STEPS,
    BACKGROUND_COLOR_RANGE,
    BACKGROUND_DECOY_CLEARANCE_WIDTH,
    BACKGROUND_HUE_BUDGET_SCALE,
    BACKGROUND_HUE_TARGETS,
    BACKGROUND_LARGE_BLOB_PROBABILITY,
    BACKGROUND_LARGE_BLOB_RADIUS,
    BACKGROUND_LARGE_BLOB_STEPS,
    BACKGROUND_MIN_CLUSTER_PIXELS,
    BACKGROUND_NOISE_STANDARD_DEVIATION,
    BACKGROUND_SIDE_HUE_TARGET,
    CARD_HEIGHT,
    CARD_WIDTH,
    RGBColor,
)
from .decoys import (
    add_decoy_components,
)
from .masks import (
    BackgroundFruitContacts,
    component_pixel_indices,
    create_background_fruit_contacts,
    expand_mask,
    fruit_interior_mask,
)
from .models import (
    FruitImagePosition,
)
from .svg import (
    get_card_mask,
)


def create_background_paper() -> Image.Image:
    """Make the lightly tinted paper beneath the colored marks."""
    # Per-channel base variation avoids every card sharing identical flat white.
    base_color = tuple(random.randint(*BACKGROUND_COLOR_RANGE) for _ in range(3))
    background = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), base_color)
    noise = Image.effect_noise(
        (CARD_WIDTH, CARD_HEIGHT), BACKGROUND_NOISE_STANDARD_DEVIATION
    ).convert("RGB")
    # Noise is centered around 128, so the negative offset preserves the chosen
    # base color while adding only a low-strength texture.
    return ImageChops.add(background, noise, offset=-128).convert("RGBA")


def paint_background_cluster(
    pixels,
    occupied: bytearray,
    contacts: BackgroundFruitContacts,
    color: RGBColor,
    mask: Image.Image,
    left: int,
    top: int,
) -> int:
    """Accept or reject a complete cluster, with no pixel thinning or clipping."""
    if isinstance(pixels, np.ndarray):
        # These are views into this card's buffers. A rejected cluster must
        # leave both pixels and occupancy unchanged, so validate before writing.
        painted_mask = (
            np.frombuffer(mask.tobytes(), dtype=np.uint8).reshape(
                mask.height, mask.width
            )
            != 0
        )
        painted = int(np.count_nonzero(painted_mask))
        if not painted:
            return 0
        occupied_region = np.frombuffer(occupied, dtype=np.uint8).reshape(
            CARD_HEIGHT, CARD_WIDTH
        )[top : top + mask.height, left : left + mask.width]
        if np.any(occupied_region[painted_mask]):
            return 0
        label_region = np.frombuffer(contacts.labels, dtype=np.uint8).reshape(
            CARD_HEIGHT, CARD_WIDTH
        )[top : top + mask.height, left : left + mask.width]
        memberships = int(np.bitwise_or.reduce(label_region[painted_mask], initial=0))
        touched = [
            index for index in range(len(contacts.counts)) if memberships & (1 << index)
        ]
        if any(contacts.counts[index] >= contacts.limits[index] for index in touched):
            return 0
        occupied_region[painted_mask] = 1
        pixels[top : top + mask.height, left : left + mask.width][painted_mask] = (
            color + (255,)
        )
        for index in touched:
            contacts.counts[index] += 1
        return painted
    width, height = mask.size
    origin = top * CARD_WIDTH + left
    # Native mask intersection rejects collisions before allocating hundreds
    # of absolute pixel indices. Only copy the candidate's small rectangle.
    occupied_region = Image.frombytes(
        "L",
        mask.size,
        b"".join(
            occupied[origin + y * CARD_WIDTH : origin + y * CARD_WIDTH + width]
            for y in range(height)
        ),
    )
    if ImageChops.multiply(mask, occupied_region).getbbox():
        return 0
    indices = component_pixel_indices(mask, left, top, CARD_WIDTH)
    if not indices:
        return 0
    memberships = 0
    for index in indices:
        memberships |= contacts.labels[index]
    touched = [
        index for index in range(len(contacts.counts)) if memberships & (1 << index)
    ]
    if any(contacts.counts[index] >= contacts.limits[index] for index in touched):
        return 0
    for index in indices:
        occupied[index] = 1
        pixels[index % CARD_WIDTH, index // CARD_WIDTH] = color + (255,)
    for index in touched:
        contacts.counts[index] += 1
    return len(indices)


def paint_background_blob(
    pixels,
    occupied: bytearray,
    contacts: BackgroundFruitContacts,
    color: RGBColor,
    needed: int,
    added: int,
    rng: np.random.Generator | None = None,
) -> tuple[int, int]:
    """Build a connected walk, then accept or reject its entire painted mask."""
    # Larger walks add irregular islands without copying a fruit silhouette.
    large = random.random() < BACKGROUND_LARGE_BLOB_PROBABILITY
    steps = BACKGROUND_LARGE_BLOB_STEPS if large else BACKGROUND_BLOB_STEPS
    radius = BACKGROUND_LARGE_BLOB_RADIUS if large else BACKGROUND_BLOB_RADIUS
    # Short 3/4px steps and 3-7/4-10px brushes overlap into fuller islands.
    # Wider minimum footprints reduce tiny flecks and the number of separate
    # walks needed to fill the same hue deficit. Keep the radius bounded so
    # these balancing patches stay smaller than the realistic fruit decoys.
    stride = 4 if large else 3
    brush_minimum = 4 if large else 3
    brush_limit = 10 if large else 7
    size = radius * 2 + brush_limit + 1
    deficit = needed - added
    # A walk cannot paint more than one brush plus its connector per step.
    # Skip full-mask histograms until even that upper bound reaches the deficit.
    maximum_step_area = brush_limit**2 + (stride + brush_minimum) ** 2
    if rng is None:
        # Standalone callers retain the standard random source. Normal card
        # generation passes one independent compiled generator for all walks.
        values = (
            (
                (random.randint(-stride, stride), random.randint(-stride, stride)),
                (
                    random.randint(brush_minimum, brush_limit),
                    random.randint(brush_minimum, brush_limit),
                ),
            )
            for _ in range(steps)
        )
    else:
        # Batch the many bounded random draws in NumPy's compiled sampler.
        values = zip(
            rng.integers(-stride, stride + 1, size=(steps, 2), dtype=np.int16).tolist(),
            rng.integers(
                brush_minimum, brush_limit + 1, size=(steps, 2), dtype=np.int16
            ).tolist(),
        )
    mask, used_steps = rasterize_background_walk(
        values, size, radius, brush_minimum, deficit, maximum_step_area
    )
    mask = mask.crop(mask.getbbox())
    left = random.randrange(CARD_WIDTH - mask.width + 1)
    top = random.randrange(CARD_HEIGHT - mask.height + 1)
    painted = paint_background_cluster(
        pixels, occupied, contacts, color, mask, left, top
    )
    # Keep the final footprint whole even if it modestly exceeds the deficit.
    return added + painted, used_steps


def rasterize_background_walk(
    values, size, radius, brush_minimum, deficit, maximum_step_area
):
    """Use Pillow's compiled drawing primitives for a connected binary walk."""
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    # Pillow is pinned in requirements. For L masks, ink is already the byte
    # 255; the public wrappers would repeat that conversion for every stroke.
    line = draw.draw.draw_lines
    rectangle = draw.draw.draw_rectangle
    histogram = mask.histogram
    x = y = radius
    maximum = radius * 2
    used_steps = 0
    for (delta_x, delta_y), (brush_width, brush_height) in values:
        previous = (x, y)
        x += delta_x
        y += delta_y
        if x < 0:
            x = 0
        elif x > maximum:
            x = maximum
        if y < 0:
            y = 0
        elif y > maximum:
            y = maximum
        # A solid connector keeps even diagonal steps in one component.
        line((*previous, x, y), 255, brush_minimum)
        rectangle((x, y, x + brush_width - 1, y + brush_height - 1), 255, 1)
        used_steps += 1
        if used_steps * maximum_step_area >= deficit and histogram()[255] >= deficit:
            break
    return mask, used_steps


def fill_background_hue(
    pixels,
    occupied: bytearray,
    contacts: BackgroundFruitContacts,
    color: RGBColor,
    needed: int,
    rng: np.random.Generator | None = None,
) -> None:
    """Fill one color deficit with small walks and a bounded retry budget."""
    added = 0
    attempts = 0
    palette = get_background_bin_palettes()[get_color_hue_bin(color)] or (color,)
    # Whole-cluster rejection can leave a bin below target. Bound retries and
    # leave a small residual instead of creating loose pixels to force equality.
    while needed - added >= BACKGROUND_MIN_CLUSTER_PIXELS and attempts < needed * 4:
        added, steps = paint_background_blob(
            pixels, occupied, contacts, random.choice(palette), needed, added, rng
        )
        attempts += steps


def balance_background_hues(
    background: Image.Image,
    occupied: bytearray,
    contacts: BackgroundFruitContacts,
    hue_counts: list[int],
) -> None:
    """Top up hue bins using the actual fruit and textured decoy colors."""
    main_colors, main_hues = get_background_palette()
    # Paint one contiguous buffer in compiled array operations, then copy it
    # back once rather than assigning every cluster pixel through Python.
    pixels = np.array(background)
    # A per-card stream avoids shared generator state across concurrent calls.
    # Seed it from the existing seeded Python RNG so test batches still replay.
    rng = np.random.Generator(np.random.PCG64(random.getrandbits(128)))
    # NumPy samples walk steps; the standard RNG still chooses palettes,
    # budgets and placements. Keep the NumPy stream local to this card.
    background_bins = get_background_hue_bins(main_hues)
    # Vary the total colored area independently of fruit/count, so the paper
    # fraction does not undo the ambiguity of the normalized hue histogram.
    budget_multiplier = random.uniform(0.85, 1.15)
    for hue_bin in random.sample(background_bins, len(background_bins)):
        color = get_background_bin_color(hue_bin, main_colors, main_hues)
        # +/-35% per-bin budgets and random bin order vary the contribution and
        # which colors claim free pixels first. This reduces exact color/count
        # signatures but does not make a shared noise distribution unlearnable.
        target_pixels = round(
            BACKGROUND_HUE_TARGETS.get(hue_bin, BACKGROUND_SIDE_HUE_TARGET)
            * BACKGROUND_HUE_BUDGET_SCALE
            * budget_multiplier
            * random.uniform(0.65, 1.35)
        )
        needed = max(0, target_pixels - hue_counts[hue_bin])
        # Only add deficits. Removing fruit/decoy pixels to force exact equality
        # would damage their shapes; overfull bins can still retain some signal.
        fill_background_hue(pixels, occupied, contacts, color, needed, rng)
    background.paste(Image.fromarray(pixels))


def add_background_blobs(
    background: Image.Image,
    foreground: Image.Image,
    fruit: str,
    amount: int,
    fruit_positions: list[FruitImagePosition],
) -> None:
    """Balance hue bins after placing the larger counting distractions."""
    hue_counts = count_foreground_hues(foreground)

    # Keep clusters whole and cap nearby contacts per fruit. Rejected walks
    # consume no pixels, so their hue budget can be filled elsewhere instead.
    occupied = bytearray(
        opacity > 0 for opacity in foreground.getchannel("A").getdata()
    )
    contacts = create_background_fruit_contacts(foreground, fruit_positions)
    exclusion = expand_mask(
        fruit_interior_mask(foreground), BACKGROUND_DECOY_CLEARANCE_WIDTH
    ).tobytes()
    add_decoy_components(background, occupied, fruit, exclusion, hue_counts, amount)
    balance_background_hues(background, occupied, contacts, hue_counts)


def create_noisy_background(
    foreground: Image.Image,
    fruit: str,
    amount: int,
    fruit_positions: list[FruitImagePosition],
) -> Image.Image:
    """Balance fruit-colored background pixels behind and around the fruit.

    Args:
        foreground (Image.Image): Rendered fruit layer.
        fruit (str): Actual fruit whose palette is excluded from large decoys.
        amount (int): Actual fruit count; decoy counts are sampled independently.
        fruit_positions (list[FruitImagePosition]): Rendered bounds for contact limits.

    Returns:
        Image.Image: RGBA background with transparent rounded corners.
    """
    background = create_background_paper()
    add_background_blobs(background, foreground, fruit, amount, fruit_positions)
    # Clip the background to the card's rounded corners.
    background.putalpha(get_card_mask())
    return background
