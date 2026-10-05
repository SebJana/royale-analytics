"""Shared hue budgets and source-derived colors and textures."""

import colorsys
import random
from functools import lru_cache

from PIL import Image, ImageChops

from .assets import (
    get_fruit_colors,
    get_main_fruit_color,
)
from ..halli_galli_card import (
    AVAILABLE_FRUITS,
    BACKGROUND_HUE_BINS,
    BACKGROUND_HUE_TARGETS,
    ColorPalette,
    RGBColor,
)
from .layout import (
    clamp,
)


def get_background_palette() -> tuple[list[RGBColor], list[float]]:
    """Use one source color and hue for each fruit type."""
    # Use nearby shades of each fruit's one main color. Fill the same hue bins
    # on every card so the actual fruit adds less of a unique histogram peak.
    main_colors = [get_main_fruit_color(name) for name in AVAILABLE_FRUITS]
    main_hues = [
        colorsys.rgb_to_hsv(*(channel / 255 for channel in color))[0]
        for color in main_colors
    ]
    return main_colors, main_hues


@lru_cache(maxsize=16384)
def get_color_hsv(color: RGBColor) -> tuple[int, int, int]:
    """Use the same quantized HSV conversion as final-image histogram probes."""
    return Image.new("RGB", (1, 1), color).convert("HSV").getpixel((0, 0))


def get_color_hue_bin(color: RGBColor) -> int:
    """Map a source or painted color to the shared background hue bins."""
    hue = get_color_hsv(color)[0]
    return min(BACKGROUND_HUE_BINS - 1, hue * BACKGROUND_HUE_BINS // 256)


def count_foreground_hues(foreground: Image.Image) -> list[int]:
    """Count painted vivid pixels before setting background hue targets."""
    foreground_counts = [0] * BACKGROUND_HUE_BINS
    hue, saturation, value = foreground.convert("RGB").convert("HSV").split()
    mask = ImageChops.multiply(
        foreground.getchannel("A").point(lambda pixel: 255 if pixel >= 200 else 0),
        ImageChops.multiply(
            saturation.point(lambda pixel: 255 if pixel >= 90 else 0),
            value.point(lambda pixel: 255 if pixel >= 60 else 0),
        ),
    )
    for hue_value, count in enumerate(hue.histogram(mask)):
        foreground_counts[hue_value * BACKGROUND_HUE_BINS // 256] += count
    return foreground_counts


def get_background_hue_bins(main_hues: list[float]) -> list[int]:
    """Cover target bins, source fills, and neighboring dominant hues."""
    # Two bins each way cover source shades and the fruit's small hue shifts.
    # Wrap offsets around the circle so reds on both ends receive background marks.
    return sorted(
        set(BACKGROUND_HUE_TARGETS)
        | {
            index
            for index, palette in enumerate(get_background_bin_palettes())
            if palette
        }
        | {
            (round(hue * BACKGROUND_HUE_BINS) + offset) % BACKGROUND_HUE_BINS
            for hue in main_hues
            for offset in range(-2, 3)
        }
    )


def get_background_bin_color(
    hue_bin: int, main_colors: list[RGBColor], main_hues: list[float]
) -> tuple[int, int, int]:
    """Sample a real source fill so background colors match the fruit palette."""
    source_colors = get_background_bin_palettes()[hue_bin]
    if source_colors:
        return random.choice(source_colors)
    # For an empty bin, use its center (+0.5) and borrow saturation/value from
    # the nearest fruit hue. Keep colored fills instead of pale paper-like noise.
    target_hue = (hue_bin + 0.5) / BACKGROUND_HUE_BINS
    palette_index = min(
        range(len(main_hues)),
        key=lambda index: min(
            abs(target_hue - main_hues[index]),
            1 - abs(target_hue - main_hues[index]),
        ),
    )
    _, saturation, value = colorsys.rgb_to_hsv(
        *(channel / 255 for channel in main_colors[palette_index])
    )
    return tuple(
        round(channel * 255)
        for channel in colorsys.hsv_to_rgb(target_hue, saturation, value)
    )


@lru_cache(maxsize=1)
def get_background_bin_palettes() -> tuple[ColorPalette, ...]:
    """Cache source colors by hue once for all background marks."""
    palettes = [[] for _ in range(BACKGROUND_HUE_BINS)]
    for fruit in AVAILABLE_FRUITS:
        for color in get_fruit_colors(fruit):
            _, saturation, value = get_color_hsv(color)
            if saturation >= 90 and value >= 60:
                palettes[get_color_hue_bin(color)].append(color)
    return tuple(tuple(colors) for colors in palettes)


def get_component_shade(color: RGBColor, brightness: float) -> RGBColor:
    """Vary brightness and saturation while retaining the fruit's main hue."""
    hue, saturation, value = colorsys.rgb_to_hsv(*(channel / 255 for channel in color))
    # +/-15% saturation adds shade variety. Floors keep shades colored rather
    # than gray; the final quantized colors determine histogram accounting.
    saturation = clamp(saturation * random.uniform(0.85, 1.15), 0.35, 1)
    value = clamp(value * brightness, 0.25, 1)
    return tuple(
        round(channel * 255) for channel in colorsys.hsv_to_rgb(hue, saturation, value)
    )


@lru_cache(maxsize=4)
def get_decoy_palette(fruit: str) -> ColorPalette:
    """Keep the common fill shades, leaving leaves and stems out of blobs."""
    main_color = get_main_fruit_color(fruit)
    main_hue = colorsys.rgb_to_hsv(*(channel / 255 for channel in main_color))[0]
    palette = []
    # Cache one palette per supported fruit. A 0.08 hue distance (~29 degrees)
    # keeps nearby fill shades while excluding differently colored leaves/stems.
    for color in get_fruit_colors(fruit):
        hue = colorsys.rgb_to_hsv(*(channel / 255 for channel in color))[0]
        if min(abs(hue - main_hue), 1 - abs(hue - main_hue)) < 0.08:
            palette.append(color)
    return tuple(palette) or (main_color,)


def create_component_texture(
    size: tuple[int, int], palette: ColorPalette, brightness: float
) -> Image.Image:
    """Blend source fill shades into broad patches for a blob surface."""
    # 13-21px patches create shade changes across the blob without pixel-level
    # static. Bilinear resizing blends the boundaries into one painted surface.
    patch_size = random.randint(13, 21)
    columns = (size[0] + patch_size - 1) // patch_size
    rows = (size[1] + patch_size - 1) // patch_size
    patches = Image.new("RGB", (columns, rows))
    patches.putdata(
        [
            get_component_shade(random.choice(palette), brightness)
            for _ in range(columns * rows)
        ]
    )
    return patches.resize(size, Image.Resampling.BILINEAR)


def get_contrasting_fruits(fruit: str) -> tuple[str, ...]:
    """Exclude the actual fruit and the overlapping banana/orange palettes."""
    # Banana and orange fills overlap in hue: neither is a useful contrasting
    # decoy for the other. Purple and red stay clearly different on both cards.
    return tuple(
        source
        for source in AVAILABLE_FRUITS
        if source != fruit
        and not (fruit in ("banana", "orange") and source in ("banana", "orange"))
    )
