"""Paper texture and whole-cluster filling of shared hue deficits.

Guarantees:
- Balancing clusters are whole, connected walk shapes at random positions,
  never overlapping fruit, decoys or each other (touching is allowed). Each
  fruit touches at most its sampled contact limit
  (BACKGROUND_FRUIT_CONTACT_LIMIT). A rejected cluster is dropped whole,
  never thinned or clipped.
- Hue deficits are filled in shuffled bin order with full-length walks; a
  bin's last cluster is one of the smallest shapes that reach the rest of
  its deficit.
- The first card waits for the shape library (~0.5 s); afterwards every
  BACKGROUND_WALK_REFRESH_CARDS cards one card replaces
  BACKGROUND_WALK_REFRESH_COUNT shapes (~20 ms).

Cards stamp stored walks in one of eight orientations, since drawing every
walk per card would dominate a card's render time. Shapes recur across
cards, so the library keeps replacing itself: a shape is gone before an
observer collects many cards with it.

Clusters are placed at random rather than packed, so same-hue clusters can
touch and merge into larger, irregular patches.

The media worker renders on one thread. The library stays safe for several
rendering threads anyway: replacement shapes are built outside the lock, and
a refresh swaps in new lists, so a card keeps the consistent lists it read.

random.seed() does not replay a card: the library and the paper tiles carry
state from earlier cards, and Pillow's paper noise has its own generator.
"""

import bisect
import random
import threading
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw

from .colors import (
    count_foreground_hues,
    get_background_bin_color,
    get_background_bin_palettes,
    get_background_hue_bins,
    get_background_palette,
    get_color_hue_bin,
)
from .constants import (
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
    BACKGROUND_PAPER_TILES,
    BACKGROUND_SIDE_HUE_TARGET,
    BACKGROUND_WALK_LIBRARY_SIZE,
    BACKGROUND_WALK_REFRESH_CARDS,
    BACKGROUND_WALK_REFRESH_COUNT,
    CARD_HEIGHT,
    CARD_WIDTH,
)
from .decoys import (
    add_decoy_components,
)
from .masks import (
    CARD_SHAPE,
    BackgroundFruitContacts,
    create_background_fruit_contacts,
    fruit_clearance,
)
from .models import (
    FruitImagePosition,
)
from .svg import (
    get_card_mask,
)

# Share of library shapes that are cut short. They finish a bin's deficit;
# every other cluster is a full walk.
SHORT_WALK_SHARE = 1 / 3

# Budget units one placement attempt costs, against 4 per missing pixel, so a
# bin gives up after about needed / 15 attempts on a crowded card.
WALK_ATTEMPT_COST = 60


def rasterize_walk(steps: int, large: bool, rng: np.random.Generator) -> np.ndarray:
    """Draw one connected brush walk with Pillow's compiled primitives.

    Args:
        steps (int): Walk length; shorter walks give smaller clusters.
        large (bool): Broader brushes and a wider radius.
        rng (np.random.Generator): Random stream for the steps and brushes.

    Returns:
        np.ndarray: Boolean mask cropped to the painted pixels.
    """
    radius = BACKGROUND_LARGE_BLOB_RADIUS if large else BACKGROUND_BLOB_RADIUS
    # Short 3/4px steps and 3-7/4-10px brushes overlap into fuller islands.
    # Wider minimum footprints reduce tiny flecks. Keep the radius bounded so
    # these balancing patches stay smaller than the realistic fruit decoys.
    stride = 4 if large else 3
    brush_minimum = 4 if large else 3
    brush_limit = 10 if large else 7
    size = radius * 2 + brush_limit + 1
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    # Pillow is pinned in requirements. For L masks, ink is already the byte
    # 255; the public wrappers would repeat that conversion for every stroke.
    line = draw.draw.draw_lines
    rectangle = draw.draw.draw_rectangle
    x = y = radius
    maximum = radius * 2
    moves = rng.integers(-stride, stride + 1, size=(steps, 2)).tolist()
    brushes = rng.integers(brush_minimum, brush_limit + 1, size=(steps, 2)).tolist()
    for (delta_x, delta_y), (brush_width, brush_height) in zip(moves, brushes):
        previous = (x, y)
        x = min(max(x + delta_x, 0), maximum)
        y = min(max(y + delta_y, 0), maximum)
        # A solid connector keeps even diagonal steps in one component.
        line((*previous, x, y), 255, brush_minimum)
        rectangle((x, y, x + brush_width - 1, y + brush_height - 1), 255, 1)
    return np.asarray(mask.crop(mask.getbbox())) > 0


def create_walk_shape(short: bool, rng: np.random.Generator) -> np.ndarray:
    """One full walk, or one cut at a random length for finishing a bin."""
    # Roughly one in three walks is broader. Mixing sizes avoids one uniform
    # speck size and adds irregular connected regions for component detectors.
    large = random.random() < BACKGROUND_LARGE_BLOB_PROBABILITY
    steps = BACKGROUND_LARGE_BLOB_STEPS if large else BACKGROUND_BLOB_STEPS
    return rasterize_walk(random.randint(1, steps) if short else steps, large, rng)


def create_paper_tile() -> np.ndarray:
    """Grayscale texture centered on 0, added to each card's tint."""
    noise = Image.effect_noise(
        (CARD_WIDTH, CARD_HEIGHT), BACKGROUND_NOISE_STANDARD_DEVIATION
    )
    return np.asarray(noise, dtype=np.int16) - 128


@dataclass(frozen=True)
class WalkShapes:
    """One consistent set of shapes: full walks, and all shapes by size."""

    full: tuple[np.ndarray, ...]
    by_size: tuple[np.ndarray, ...]
    sizes: tuple[int, ...]


def index_walk_shapes(full: list[np.ndarray], short: list[np.ndarray]) -> WalkShapes:
    """Sort every shape by its pixel count for finishing a bin."""
    by_size = sorted(full + short, key=lambda shape: int(shape.sum()))
    return WalkShapes(
        full=tuple(full),
        by_size=tuple(by_size),
        sizes=tuple(int(shape.sum()) for shape in by_size),
    )


@dataclass
class ConfettiLibrary:
    """Walk shapes and paper tiles shared by all cards."""

    full: list[np.ndarray]
    short: list[np.ndarray]
    shapes: WalkShapes
    papers: list[np.ndarray]
    cards: int = 0
    # Next shape to replace, cycling through full and then short walks.
    next_shape: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


_library: ConfettiLibrary | None = None
_library_lock = threading.Lock()


def _new_rng() -> np.random.Generator:
    # Seeded from the standard RNG, so one generator decides the draws of a
    # card's stages instead of NumPy's global state.
    return np.random.Generator(np.random.PCG64(random.getrandbits(128)))


def get_confetti_library() -> ConfettiLibrary:
    """Return the shared library, building or refreshing it first.

    Returns:
        ConfettiLibrary: The library this card draws from.
    """
    global _library
    with _library_lock:
        if _library is None:
            rng = _new_rng()
            short_count = round(BACKGROUND_WALK_LIBRARY_SIZE * SHORT_WALK_SHARE)
            full = [
                create_walk_shape(False, rng)
                for _ in range(BACKGROUND_WALK_LIBRARY_SIZE - short_count)
            ]
            short = [create_walk_shape(True, rng) for _ in range(short_count)]
            _library = ConfettiLibrary(
                full=full,
                short=short,
                shapes=index_walk_shapes(full, short),
                papers=[create_paper_tile() for _ in range(BACKGROUND_PAPER_TILES)],
            )
    library = _library
    with library.lock:
        library.cards += 1
        refreshing = library.cards % BACKGROUND_WALK_REFRESH_CARDS == 0
        start = library.next_shape
    if not refreshing:
        return library

    # Build outside the lock so other render threads keep drawing cards.
    rng = _new_rng()
    total = len(library.full) + len(library.short)
    replacements = [
        (index % total, create_walk_shape(index % total >= len(library.full), rng))
        for index in range(start, start + BACKGROUND_WALK_REFRESH_COUNT)
    ]
    with library.lock:
        full, short = list(library.full), list(library.short)
        for index, shape in replacements:
            if index < len(full):
                full[index] = shape
            else:
                short[index - len(full)] = shape
        library.full, library.short = full, short
        library.shapes = index_walk_shapes(full, short)
        library.next_shape = (start + BACKGROUND_WALK_REFRESH_COUNT) % total
    return library


def create_background_paper(library: ConfettiLibrary) -> np.ndarray:
    """Make the lightly tinted paper beneath the colored marks."""
    # Per-channel base variation avoids every card sharing identical flat white.
    base_color = np.array(
        [random.randint(*BACKGROUND_COLOR_RANGE) for _ in range(3)], dtype=np.int16
    )
    paper = random.choice(library.papers)
    if random.random() < 0.5:
        paper = paper[::-1]
    if random.random() < 0.5:
        paper = paper[:, ::-1]
    return np.clip(base_color + paper[..., None], 0, 255).astype(np.uint8)


def choose_walk_shape(shapes: WalkShapes, remaining: int) -> np.ndarray:
    """A full walk, or one of the smallest shapes reaching a smaller deficit.

    Args:
        shapes (WalkShapes): The library's shapes.
        remaining (int): Pixels this bin still needs.

    Returns:
        np.ndarray: The shape in one of its eight orientations.
    """
    shape = random.choice(shapes.full)
    size = int(shape.sum())
    if size > remaining:
        # Overshooting by a whole walk would leave the bin unbalanced. Pick
        # among the few smallest shapes that cover it, so the finish varies.
        first = bisect.bisect_left(shapes.sizes, remaining)
        first = min(first, len(shapes.sizes) - 1)
        shape = shapes.by_size[
            random.randrange(first, min(first + 4, len(shapes.sizes)))
        ]
    shape = np.rot90(shape, random.randrange(4))
    return shape[:, ::-1] if random.random() < 0.5 else shape


def add_balancing_confetti(
    background: np.ndarray,
    occupied: np.ndarray,
    contacts: BackgroundFruitContacts,
    hue_counts: np.ndarray,
    library: ConfettiLibrary,
) -> None:
    """Top up hue bins with whole clusters at random free positions.

    Args:
        background (np.ndarray): Card RGB pixels, painted in place.
        occupied (np.ndarray): Fruit and decoy pixels, updated with clusters.
        contacts (BackgroundFruitContacts): Fruit surroundings and limits.
        hue_counts (np.ndarray): Vivid fruit and decoy pixels per hue bin.
        library (ConfettiLibrary): Source of the shapes.
    """
    shapes = library.shapes
    # Bit i marks fruit i's surroundings, so one OR per cluster names every
    # fruit it touches.
    contact_bits = np.zeros(CARD_SHAPE, dtype=np.uint8)
    for index, band in enumerate(contacts.bands):
        contact_bits[band] |= 1 << index
    contact_counts = [0] * len(contacts.bands)

    main_colors, main_hues = get_background_palette()
    hue_bins = get_background_hue_bins(main_hues)
    # Vary the total colored area independently of fruit/count, so the paper
    # fraction does not undo the ambiguity of the normalized hue histogram.
    budget_multiplier = random.uniform(0.85, 1.15)
    for hue_bin in random.sample(hue_bins, len(hue_bins)):
        color = get_background_bin_color(hue_bin, main_colors, main_hues)
        palette = get_background_bin_palettes()[get_color_hue_bin(color)] or (color,)
        # +/-35% per-bin budgets and random bin order vary the contribution and
        # which colors claim free pixels first.
        target_pixels = round(
            BACKGROUND_HUE_TARGETS.get(hue_bin, BACKGROUND_SIDE_HUE_TARGET)
            * BACKGROUND_HUE_BUDGET_SCALE
            * budget_multiplier
            * random.uniform(0.65, 1.35)
        )
        # Only add deficits. Removing fruit/decoy pixels to force exact equality
        # would damage their shapes; overfull bins can still retain some signal.
        needed = target_pixels - int(hue_counts[hue_bin])
        added = 0
        budget = needed * 4
        # Stop at the minimum cluster size or the retry budget, leaving a small
        # residual instead of a tiny finishing fragment.
        while needed - added >= BACKGROUND_MIN_CLUSTER_PIXELS and budget > 0:
            budget -= WALK_ATTEMPT_COST
            shape = choose_walk_shape(shapes, needed - added)
            height, width = shape.shape
            top = random.randrange(CARD_HEIGHT - height + 1)
            left = random.randrange(CARD_WIDTH - width + 1)
            window = (slice(top, top + height), slice(left, left + width))
            if np.any(occupied[window][shape]):
                continue
            touched_bits = int(np.bitwise_or.reduce(contact_bits[window][shape]))
            touched = [i for i in range(len(contact_counts)) if touched_bits >> i & 1]
            if any(contact_counts[i] >= contacts.limits[i] for i in touched):
                continue
            for i in touched:
                contact_counts[i] += 1
            occupied[window] |= shape
            background[window][shape] = random.choice(palette)
            added += int(shape.sum())


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
    library = get_confetti_library()
    rng = _new_rng()
    painted = np.asarray(foreground.getchannel("A")) > 0
    background = create_background_paper(library)
    # Measure the augmented foreground and reserve its painted pixels before
    # placing background distractions.
    hue_counts = np.array(count_foreground_hues(foreground), dtype=np.int64)
    occupied = painted.copy()
    # Reserve 17px around painted fruit for big decoys only. Small balancing
    # clusters can still occupy that space, avoiding a visible halo.
    clearance = fruit_clearance(painted, BACKGROUND_DECOY_CLEARANCE_WIDTH)
    add_decoy_components(
        background, occupied, fruit, clearance, hue_counts, amount, rng
    )
    contacts = create_background_fruit_contacts(painted, fruit_positions)
    add_balancing_confetti(background, occupied, contacts, hue_counts, library)
    image = Image.fromarray(background, "RGB").convert("RGBA")
    # Clip the background to the card's rounded corners.
    image.putalpha(get_card_mask())
    return image
