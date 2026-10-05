"""Readable surface patches and neutral overlays across fruit and paper."""

import math
import random

from PIL import Image, ImageChops, ImageDraw

from .assets import (
    get_fruit_colors,
)
from .colors import (
    create_component_texture,
    get_contrasting_fruits,
    get_decoy_palette,
)
from ..halli_galli_card import (
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
    fruit_interior_mask,
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


def draw_fruit_artifacts(
    draw: ImageDraw.ImageDraw,
    position: FruitImagePosition,
    own_palette: ColorPalette,
    palettes: list[ColorPalette],
) -> None:
    """Scatter flecks and short strokes over one fruit's bounding box."""
    left = max(0, int(position.x * CARD_WIDTH))
    top = max(0, int(position.y * CARD_HEIGHT))
    right = min(CARD_WIDTH - 1, int((position.x + position.width) * CARD_WIDTH))
    bottom = min(CARD_HEIGHT - 1, int((position.y + position.height) * CARD_HEIGHT))
    # More numerous 1-2px specks add fine surface noise rather than large spots.
    # Alpha 130 (~51%) keeps underlying emoji details visible beneath the marks.
    for _ in range(FRUIT_ARTIFACT_DOTS):
        x, y = random.randint(left, right), random.randint(top, bottom)
        color = choose_artifact_color(own_palette, palettes)
        draw.rectangle(
            (x, y, x + random.randint(0, 1), y + random.randint(0, 1)),
            fill=color + (130,),
        )
    # 3-8px strokes with at most 2px vertical drift stay short. Alpha 120 (~47%)
    # and a 1px width keep them from covering distinctive emoji details.
    for _ in range(FRUIT_ARTIFACT_STREAKS):
        x, y = random.randint(left, right), random.randint(top, bottom)
        color = choose_artifact_color(own_palette, palettes)
        draw.line(
            (x, y, x + random.randint(3, 8), y + random.randint(-2, 2)),
            fill=color + (120,),
            width=1,
        )


def add_fruit_patch_artifacts(
    artifacts: Image.Image,
    foreground: Image.Image,
    fruit_positions: list[FruitImagePosition],
    fruit: str,
) -> Image.Image:
    """Lay larger shaded patches over a limited part of each fruit surface."""
    alpha = foreground.getchannel("A")
    for position in fruit_positions:
        left = max(0, math.floor(position.x * CARD_WIDTH))
        top = max(0, math.floor(position.y * CARD_HEIGHT))
        right = min(CARD_WIDTH, math.ceil((position.x + position.width) * CARD_WIDTH))
        bottom = min(
            CARD_HEIGHT, math.ceil((position.y + position.height) * CARD_HEIGHT)
        )
        interior = alpha.crop((left, top, right, bottom)).point(
            lambda opacity: 255 if opacity >= 200 else 0
        )
        points = [
            (index % interior.width, index // interior.width)
            for index, opacity in enumerate(interior.getdata())
            if opacity
        ]
        remaining = int(len(points) * FRUIT_ARTIFACT_PATCH_COVERAGE)
        if remaining < 32:
            continue
        covered = Image.new("L", interior.size, 0)
        patch_count = random.randint(*FRUIT_ARTIFACT_PATCHES)
        for patch_index in range(patch_count):
            for _ in range(FRUIT_ARTIFACT_PATCH_ATTEMPTS):
                center_x, center_y = random.choice(points)
                area = (
                    remaining / (patch_count - patch_index) * random.uniform(0.55, 0.85)
                )
                aspect = random.uniform(0.65, 1.5)
                radius_x = max(3, round(math.sqrt(area * aspect / math.pi)))
                radius_y = max(3, round(math.sqrt(area / aspect / math.pi)))
                shape = Image.new("L", interior.size, 0)
                draw = ImageDraw.Draw(shape)
                draw_blob_lobe(draw, center_x, center_y, radius_x, radius_y)
                # A side lobe makes a broad uneven discoloration patch. Size
                # follows the central coverage budget rather than adding marks.
                draw_blob_lobe(
                    draw,
                    center_x + radius_x // 2,
                    center_y - radius_y // 2,
                    max(2, radius_x * 2 // 3),
                    max(2, radius_y * 2 // 3),
                )
                mask = ImageChops.multiply(shape, interior)
                painted = mask.histogram()[255]
                if (
                    not painted
                    or painted > remaining
                    or ImageChops.multiply(mask, covered).getbbox()
                ):
                    continue
                source = random.choice(get_contrasting_fruits(fruit))
                texture = create_component_texture(
                    interior.size, get_decoy_palette(source), random.uniform(0.9, 1.1)
                ).convert("RGBA")
                texture.putalpha(
                    mask.point(
                        lambda opacity: FRUIT_ARTIFACT_PATCH_ALPHA if opacity else 0
                    )
                )
                artifacts.alpha_composite(texture, (left, top))
                covered = ImageChops.lighter(covered, mask)
                remaining -= painted
                break
    return artifacts


def add_fruit_artifacts(
    foreground: Image.Image,
    fruit_positions: list[FruitImagePosition],
    fruit: str,
) -> Image.Image:
    """Overlay fine flecks, short strokes and shaded surface patches."""
    artifacts = Image.new("RGBA", foreground.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(artifacts)
    palettes = [get_fruit_colors(fruit) for fruit in AVAILABLE_FRUITS]
    own_palette = get_fruit_colors(fruit)
    for position in fruit_positions:
        draw_fruit_artifacts(draw, position, own_palette, palettes)

    artifacts = add_fruit_patch_artifacts(artifacts, foreground, fruit_positions, fruit)

    # The boxes include transparent corners; clip marks to painted fruit only.
    artifacts.putalpha(
        ImageChops.multiply(artifacts.getchannel("A"), fruit_interior_mask(foreground))
    )
    return Image.alpha_composite(foreground, artifacts)


def downsample_artifact_mask(mask: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Resample only the painted area, preserving full-image Lanczos pixels."""
    result = Image.new("L", size)
    bounds = mask.getbbox()
    if bounds is None:
        return result
    scale = mask.width // size[0]
    # Cropping on this grid keeps the sampling phase identical to resizing
    # the whole card; an arbitrary crop origin would shift antialiased edges.
    # Align with the full-image sampling grid and retain more than the 3-pixel
    # Lanczos support on every side. The unpainted remainder stays exactly zero.
    padding = 4 * scale
    left = max(0, bounds[0] // scale * scale - padding)
    top = max(0, bounds[1] // scale * scale - padding)
    right = min(mask.width, (bounds[2] + scale - 1) // scale * scale + padding)
    bottom = min(mask.height, (bounds[3] + scale - 1) // scale * scale + padding)
    resized = mask.crop((left, top, right, bottom)).resize(
        ((right - left) // scale, (bottom - top) // scale), Image.Resampling.LANCZOS
    )
    result.paste(resized, (left // scale, top // scale))
    return result


def add_card_artifacts(
    card: Image.Image,
    foreground: Image.Image,
    fruit_positions: list[FruitImagePosition],
) -> Image.Image:
    """Draw neutral rings and long strokes across the entire composed card.

    Reject overly occluding marks whole, retaining continuous strokes rather
    than cutting them away at fruit boundaries. Placement is class independent.
    """
    scale = 2
    size = (card.width * scale, card.height * scale)
    artifacts = Image.new("RGBA", card.size)
    covered = Image.new("L", card.size)
    interior = foreground.getchannel("A").point(
        lambda value: 255 if value >= 200 else 0
    )
    fruit_regions = []
    for position in fruit_positions:
        bounds = (
            max(0, math.floor(position.x * card.width)),
            max(0, math.floor(position.y * card.height)),
            min(card.width, math.ceil((position.x + position.width) * card.width)),
            min(card.height, math.ceil((position.y + position.height) * card.height)),
        )
        pixels = interior.crop(bounds)
        fruit_regions.append((bounds, pixels, pixels.histogram()[255]))
    # Give the broad strokes first use of the coverage budget; rings fill the
    # remaining space without forcing excessive coverage on crowded cards.
    kinds = ["line"] * random.randint(*CARD_ARTIFACT_LINES)
    kinds += ["ring"] * random.randint(*CARD_ARTIFACT_RINGS)
    for kind in kinds:
        for _ in range(CARD_ARTIFACT_ATTEMPTS):
            mask = Image.new("L", size)
            draw = ImageDraw.Draw(mask)
            width = random.randint(2, 4) * scale
            if kind == "ring":
                radius = random.randint(18, 50)
                x, y = random.randrange(card.width), random.randrange(card.height)
                draw.ellipse(
                    tuple(
                        value * scale
                        for value in (x - radius, y - radius, x + radius, y + radius)
                    ),
                    outline=255,
                    width=width,
                )
            else:
                width = random.randint(*CARD_ARTIFACT_LINE_WIDTH) * scale
                angle = random.uniform(0, math.pi)
                x, y = random.randrange(card.width), random.randrange(card.height)
                length = math.hypot(card.width, card.height)
                dx, dy = math.cos(angle) * length, math.sin(angle) * length
                amplitude = random.uniform(*CARD_ARTIFACT_LINE_AMPLITUDE)
                cycles = random.uniform(*CARD_ARTIFACT_LINE_CYCLES)
                phase = random.uniform(0, 2 * math.pi)
                points = []
                for step in range(161):
                    t = step / 160
                    wave = t * 2 * math.pi * cycles + phase
                    # A smaller second wave makes the wiggles less uniform.
                    offset = amplitude * (
                        math.sin(wave) + 0.22 * math.sin(2.3 * wave + phase)
                    )
                    points.append(
                        (
                            (x + (t - 0.5) * dx - math.sin(angle) * offset) * scale,
                            (y + (t - 0.5) * dy + math.cos(angle) * offset) * scale,
                        )
                    )
                draw.line(points, fill=255, width=width, joint="curve")
            mask = downsample_artifact_mask(mask, card.size)
            footprint = mask.point(lambda value: 255 if value >= 16 else 0)
            candidate = ImageChops.lighter(covered, footprint)
            if (
                candidate.histogram()[255]
                > card.width * card.height * CARD_ARTIFACT_COVERAGE
            ):
                continue
            if any(
                ImageChops.multiply(candidate.crop(bounds), pixels).histogram()[255]
                > count * CARD_ARTIFACT_FRUIT_COVERAGE
                for bounds, pixels, count in fruit_regions
                if count
            ):
                continue
            gray = random.randint(90, 145)
            opacity = random.randint(115, 165)
            layer = Image.new("RGBA", card.size, (gray, gray, gray, 0))
            layer.putalpha(mask.point(lambda value: value * opacity // 255))
            artifacts.alpha_composite(layer)
            covered = candidate
            break
    return Image.alpha_composite(card, artifacts)
