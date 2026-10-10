"""Readable surface patches and neutral overlays across fruit and paper."""

import math
import random

import numpy as np
from PIL import Image, ImageDraw

from .assets import (
    get_fruit_colors,
)
from .colors import (
    create_component_texture,
    get_fruit_color_array,
    get_contrasting_fruits,
    get_decoy_palette,
)
from .constants import (
    AVAILABLE_FRUITS,
    CARD_ARTIFACT_ATTEMPTS,
    CARD_ARTIFACT_COVERAGE,
    CARD_ARTIFACT_FRUIT_COVERAGE,
    CARD_ARTIFACT_LINES,
    CARD_ARTIFACT_LINE_AMPLITUDE,
    CARD_ARTIFACT_LINE_CYCLES,
    CARD_ARTIFACT_LINE_WIDTH,
    CARD_ARTIFACT_RINGS,
    CARD_HEIGHT,
    CARD_WIDTH,
    ColorPalette,
    FRUIT_ARTIFACT_DOTS,
    FRUIT_ARTIFACT_PATCHES,
    FRUIT_ARTIFACT_PATCH_ALPHA,
    FRUIT_ARTIFACT_PATCH_ATTEMPTS,
    FRUIT_ARTIFACT_PATCH_COVERAGE,
    FRUIT_ARTIFACT_STREAKS,
    RGBColor,
)
from .decoys import (
    draw_blob_lobe,
)
from .masks import (
    CARD_SHAPE,
    fruit_pixel_box,
)
from .models import (
    FruitImagePosition,
)


def choose_artifact_color(
    own_palette: ColorPalette, palettes: list[ColorPalette]
) -> RGBColor:
    """Keep most fruit detail in its own palette, with a few rival colors."""
    # 80% own-color marks preserve recognition; 20% sampled from all palettes
    # mix some rival colors into the surface without obscuring the emoji.
    return random.choice(
        own_palette if random.random() < 0.8 else random.choice(palettes)
    )


def draw_fruit_specks(
    pixels: np.ndarray,
    box: tuple[int, int, int, int],
    fruit: str,
    rng: np.random.Generator,
) -> None:
    """Scatter 1-2px specks over one fruit's box, all at once."""
    left, top, right, bottom = box
    count = FRUIT_ARTIFACT_DOTS
    xs = rng.integers(left, max(left + 1, right), size=count)
    ys = rng.integers(top, max(top + 1, bottom), size=count)
    # Same split as choose_artifact_color: 80% own palette, 20% from a
    # uniformly chosen fruit's palette.
    own = get_fruit_color_array(fruit)
    colors = own[rng.integers(len(own), size=count)]
    rival = rng.random(count) >= 0.8
    sources = rng.integers(len(AVAILABLE_FRUITS), size=count)
    for index, name in enumerate(AVAILABLE_FRUITS):
        chosen = rival & (sources == index)
        palette = get_fruit_color_array(name)
        colors[chosen] = palette[rng.integers(len(palette), size=int(chosen.sum()))]
    grow_x = rng.integers(0, 2, size=count)
    grow_y = rng.integers(0, 2, size=count)
    # Alpha 130 (~51%) keeps underlying emoji details visible beneath the marks.
    for dx in (0, 1):
        for dy in (0, 1):
            keep = (grow_x >= dx) & (grow_y >= dy)
            px = np.minimum(xs[keep] + dx, CARD_WIDTH - 1)
            py = np.minimum(ys[keep] + dy, CARD_HEIGHT - 1)
            pixels[py, px, :3] = colors[keep]
            pixels[py, px, 3] = 130


def draw_fruit_patches(
    pixels: np.ndarray,
    box: tuple[int, int, int, int],
    interior: np.ndarray,
    fruit: str,
) -> None:
    """Lay larger shaded patches over a limited part of one fruit's surface."""
    left, top, right, bottom = box
    box_interior = interior[top:bottom, left:right]
    points = np.argwhere(box_interior)
    remaining = int(len(points) * FRUIT_ARTIFACT_PATCH_COVERAGE)
    if remaining < 32:
        return
    covered = np.zeros_like(box_interior)
    patch_count = random.randint(*FRUIT_ARTIFACT_PATCHES)
    for patch_index in range(patch_count):
        for _ in range(FRUIT_ARTIFACT_PATCH_ATTEMPTS):
            center_y, center_x = (
                int(value) for value in points[random.randrange(len(points))]
            )
            area = remaining / (patch_count - patch_index) * random.uniform(0.55, 0.85)
            aspect = random.uniform(0.65, 1.5)
            radius_x = max(3, round(math.sqrt(area * aspect / math.pi)))
            radius_y = max(3, round(math.sqrt(area / aspect / math.pi)))
            # A side lobe makes a broad uneven discoloration patch. Size
            # follows the central coverage budget rather than adding marks.
            lobe_x, lobe_y = center_x + radius_x // 2, center_y - radius_y // 2
            lobe_radius_x = max(2, radius_x * 2 // 3)
            lobe_radius_y = max(2, radius_y * 2 // 3)
            # Draw only inside the box of both lobes, then clip to the fruit.
            x0 = max(0, min(center_x - radius_x, lobe_x - lobe_radius_x))
            y0 = max(0, min(center_y - radius_y, lobe_y - lobe_radius_y))
            x1 = min(right - left, max(center_x + radius_x, lobe_x + lobe_radius_x) + 1)
            y1 = min(bottom - top, max(center_y + radius_y, lobe_y + lobe_radius_y) + 1)
            if x1 <= x0 or y1 <= y0:
                continue
            shape = Image.new("L", (x1 - x0, y1 - y0))
            draw = ImageDraw.Draw(shape)
            draw_blob_lobe(draw, center_x - x0, center_y - y0, radius_x, radius_y)
            draw_blob_lobe(draw, lobe_x - x0, lobe_y - y0, lobe_radius_x, lobe_radius_y)
            mask = np.zeros_like(box_interior)
            mask[y0:y1, x0:x1] = np.asarray(shape) > 0
            mask &= box_interior
            painted = int(mask.sum())
            if not painted or painted > remaining or np.any(mask & covered):
                continue
            source = random.choice(get_contrasting_fruits(fruit))
            texture = np.asarray(
                create_component_texture(
                    (right - left, bottom - top),
                    get_decoy_palette(source),
                    random.uniform(0.9, 1.1),
                )
            )
            region = pixels[top:bottom, left:right]
            region[mask, :3] = texture[mask]
            # Translucency leaves the underlying detail visible.
            region[mask, 3] = FRUIT_ARTIFACT_PATCH_ALPHA
            covered |= mask
            remaining -= painted
            break


def add_fruit_artifacts(
    foreground: Image.Image,
    fruit_positions: list[FruitImagePosition],
    fruit: str,
    rng: np.random.Generator,
) -> Image.Image:
    """Overlay fine flecks, short strokes and shaded surface patches."""
    alpha = np.asarray(foreground.getchannel("A"))
    interior = alpha >= 200
    marks = Image.new("RGBA", foreground.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(marks)
    palettes = [get_fruit_colors(name) for name in AVAILABLE_FRUITS]
    own_palette = get_fruit_colors(fruit)
    boxes = [fruit_pixel_box(position) for position in fruit_positions]
    for left, top, right, bottom in boxes:
        # 3-8px strokes with at most 2px vertical drift stay short. Alpha 120
        # (~47%) and a 1px width keep them from covering emoji details.
        for _ in range(FRUIT_ARTIFACT_STREAKS):
            x = random.randint(left, max(left, right - 1))
            y = random.randint(top, max(top, bottom - 1))
            color = choose_artifact_color(own_palette, palettes)
            draw.line(
                (x, y, x + random.randint(3, 8), y + random.randint(-2, 2)),
                fill=color + (120,),
                width=1,
            )
    pixels = np.array(marks)
    for box in boxes:
        draw_fruit_specks(pixels, box, fruit, rng)
        draw_fruit_patches(pixels, box, interior, fruit)
    # The boxes include transparent corners; clip marks to painted fruit only.
    pixels[..., 3] = (pixels[..., 3].astype(np.uint16) * alpha // 255).astype(np.uint8)
    return Image.alpha_composite(foreground, Image.fromarray(pixels, "RGBA"))


def draw_card_mark(kind: str, size: tuple[int, int]) -> Image.Image:
    """Draw one neutral ring or long squiggly stroke as a card-sized mask."""
    mask = Image.new("L", size)
    draw = ImageDraw.Draw(mask)
    width, height = size
    if kind == "ring":
        radius = random.randint(18, 50)
        x, y = random.randrange(width), random.randrange(height)
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            outline=255,
            width=random.randint(2, 4),
        )
        return mask
    line_width = random.randint(*CARD_ARTIFACT_LINE_WIDTH)
    angle = random.uniform(0, math.pi)
    x, y = random.randrange(width), random.randrange(height)
    length = math.hypot(width, height)
    dx, dy = math.cos(angle) * length, math.sin(angle) * length
    amplitude = random.uniform(*CARD_ARTIFACT_LINE_AMPLITUDE)
    cycles = random.uniform(*CARD_ARTIFACT_LINE_CYCLES)
    phase = random.uniform(0, 2 * math.pi)
    t = np.linspace(0, 1, 81)
    wave = t * 2 * math.pi * cycles + phase
    # A smaller second wave makes the wiggles less uniform.
    offset = amplitude * (np.sin(wave) + 0.22 * np.sin(2.3 * wave + phase))
    xs = x + (t - 0.5) * dx - math.sin(angle) * offset
    ys = y + (t - 0.5) * dy + math.cos(angle) * offset
    draw.line(
        list(zip(xs.tolist(), ys.tolist())), fill=255, width=line_width, joint="curve"
    )
    return mask


def add_card_artifacts(
    card: Image.Image,
    foreground: Image.Image,
    fruit_positions: list[FruitImagePosition],
) -> Image.Image:
    """Draw neutral rings and long strokes across the entire composed card.

    Reject overly occluding marks whole, retaining continuous strokes rather
    than cutting them away at fruit boundaries. Placement is class independent.
    Coverage is checked only inside each candidate's bounding box.
    """
    interior = np.asarray(foreground.getchannel("A")) >= 200
    layer = np.zeros(CARD_SHAPE + (4,), dtype=np.uint8)
    covered = np.zeros(CARD_SHAPE, dtype=bool)
    covered_total = 0
    # Fruit index + 1 on each fruit's interior, so one bincount per candidate
    # gives every fruit's newly covered pixels.
    owners = np.zeros(CARD_SHAPE, dtype=np.uint8)
    limits = [0.0]
    for index, position in enumerate(fruit_positions, start=1):
        left, top, right, bottom = fruit_pixel_box(position)
        region = interior[top:bottom, left:right]
        owners[top:bottom, left:right][region] = index
        limits.append(int(region.sum()) * CARD_ARTIFACT_FRUIT_COVERAGE)
    covered_by = [0] * len(limits)
    budget = CARD_WIDTH * CARD_HEIGHT * CARD_ARTIFACT_COVERAGE
    # Give the broad strokes first use of the coverage budget; rings fill the
    # remaining space without forcing excessive coverage on crowded cards.
    kinds = ["line"] * random.randint(*CARD_ARTIFACT_LINES)
    kinds += ["ring"] * random.randint(*CARD_ARTIFACT_RINGS)
    for kind in kinds:
        for _ in range(CARD_ARTIFACT_ATTEMPTS):
            mark = draw_card_mark(kind, card.size)
            bounds = mark.getbbox()
            if bounds is None:
                continue
            left, top, right, bottom = bounds
            window = (slice(top, bottom), slice(left, right))
            footprint = np.asarray(mark.crop(bounds)) > 0
            fresh = footprint & ~covered[window]
            added = int(fresh.sum())
            if covered_total + added > budget:
                continue
            per_fruit = np.bincount(owners[window][fresh], minlength=len(limits))
            if any(
                covered_by[index] + per_fruit[index] > limits[index]
                for index in range(1, len(limits))
                if limits[index]
            ):
                continue
            gray = random.randint(90, 145)
            opacity = random.randint(115, 165)
            layer[window][footprint] = (gray, gray, gray, opacity)
            covered[window] |= footprint
            covered_total += added
            for index in range(1, len(limits)):
                covered_by[index] += int(per_fruit[index])
            break
    return Image.alpha_composite(card, Image.fromarray(layer, "RGBA"))
