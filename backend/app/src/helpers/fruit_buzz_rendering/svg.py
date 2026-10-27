"""Fruit sprites: cached rasters, color variation, transforms and the card mask.

Each fruit SVG is rasterized once and kept in memory, since rasterizing per
card would dominate a card's foreground time. A card scales, recolors, flips
and rotates that raster per fruit. Recoloring shifts each pixel in HLS and
darkens bright fills for contrast.
"""

from functools import lru_cache
from io import BytesIO

import cairosvg
import numpy as np
from PIL import Image

from .constants import (
    CARD_CORNER_RADIUS,
    CARD_HEIGHT,
    CARD_WIDTH,
    FRUIT_SIZE,
    FRUIT_SPRITE_SUPERSAMPLE,
)
from .models import (
    FruitCreationPlacement,
    FruitStyle,
)


@lru_cache(maxsize=64)
def get_fruit_sprite(svg: str) -> Image.Image:
    """Rasterize one source SVG at the sprite resolution, once per process.

    Args:
        svg (str): Source fruit SVG.

    Returns:
        Image.Image: Transparent RGBA raster, FRUIT_SIZE * FRUIT_SPRITE_SUPERSAMPLE wide.
    """
    size = FRUIT_SIZE * FRUIT_SPRITE_SUPERSAMPLE
    png = cairosvg.svg2png(
        bytestring=svg.encode("utf-8"), output_width=size, output_height=size
    )
    return Image.open(BytesIO(png)).convert("RGBA")


def _rgb_to_hls(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized colorsys.rgb_to_hls for 0-1 floats."""
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    maximum = rgb.max(axis=-1)
    minimum = rgb.min(axis=-1)
    lightness = (maximum + minimum) / 2
    delta = maximum - minimum
    gray = delta == 0
    safe_delta = np.where(gray, 1, delta)
    saturation = np.where(
        lightness <= 0.5,
        delta / np.where(gray, 1, maximum + minimum),
        delta / np.where(gray, 1, 2 - maximum - minimum),
    )
    red_c = (maximum - red) / safe_delta
    green_c = (maximum - green) / safe_delta
    blue_c = (maximum - blue) / safe_delta
    hue = np.where(
        red == maximum,
        blue_c - green_c,
        np.where(green == maximum, 2 + red_c - blue_c, 4 + green_c - red_c),
    )
    hue = np.where(gray, 0, (hue / 6) % 1)
    return hue, lightness, np.where(gray, 0, saturation)


def _hls_to_rgb(
    hue: np.ndarray, lightness: np.ndarray, saturation: np.ndarray
) -> np.ndarray:
    """Vectorized colorsys.hls_to_rgb for 0-1 floats."""
    upper = np.where(
        lightness <= 0.5,
        lightness * (1 + saturation),
        lightness + saturation - lightness * saturation,
    )
    lower = 2 * lightness - upper

    def channel(shifted: np.ndarray) -> np.ndarray:
        shifted = shifted % 1
        return np.where(
            shifted < 1 / 6,
            lower + (upper - lower) * shifted * 6,
            np.where(
                shifted < 0.5,
                upper,
                np.where(
                    shifted < 2 / 3,
                    lower + (upper - lower) * (2 / 3 - shifted) * 6,
                    lower,
                ),
            ),
        )

    rgb = np.stack([channel(hue + 1 / 3), channel(hue), channel(hue - 1 / 3)], axis=-1)
    gray = saturation == 0
    rgb[gray] = lightness[gray][:, None]
    return rgb


def augment_sprite_colors(sprite: Image.Image, style: FruitStyle) -> Image.Image:
    """Apply a small hue, saturation and lightness shift to every pixel.

    Args:
        sprite (Image.Image): Scaled RGBA sprite.
        style (FruitStyle): Color changes chosen for this fruit instance.

    Returns:
        Image.Image: Recolored sprite with the same alpha.
    """
    pixels = np.asarray(sprite, dtype=np.float32) / 255
    hue, lightness, saturation = _rgb_to_hls(pixels[..., :3])
    hue = (hue + style.hue_shift) % 1
    saturation = np.clip(saturation * style.saturation_multiplier, 0, 1)
    lightness = np.clip(lightness * style.lightness_multiplier, 0, 1)
    # Only bright, colored fills need darkening against the light paper.
    # Compress their lightness toward 0.62 by retaining 35% of the excess;
    # this preserves shade variation without washing out the artwork.
    bright = (saturation >= 0.35) & (lightness >= 0.65)
    lightness = np.where(bright, 0.62 + (lightness - 0.62) * 0.35, lightness)
    rgb = _hls_to_rgb(hue, lightness, saturation)
    recolored = np.dstack([rgb, pixels[..., 3:]]) * 255
    return Image.fromarray(recolored.round().astype(np.uint8), "RGBA")


def create_fruit_sprite(
    fruit_svg: str, placement: FruitCreationPlacement
) -> Image.Image:
    """Scale, recolor, flip and rotate one fruit as the SVG transform did.

    Args:
        fruit_svg (str): Source SVG variation selected for this fruit.
        placement (FruitCreationPlacement): Position and visual style to apply.

    Returns:
        Image.Image: The transformed sprite, centered on the placement when pasted.
    """
    style = placement.style
    side = max(2, round(FRUIT_SIZE * style.scale))
    sprite = get_fruit_sprite(fruit_svg).resize((side, side), Image.Resampling.BICUBIC)
    sprite = augment_sprite_colors(sprite, style)
    if style.is_horizontally_flipped:
        sprite = sprite.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    # SVG rotate() turns clockwise on screen, PIL rotate() counterclockwise.
    # This matches the hull transform behind the click boxes in models.py.
    return sprite.rotate(
        -style.rotation_degrees, resample=Image.Resampling.BICUBIC, expand=True
    )


def render_fruit_foreground(
    fruit_svgs: list[str], placements: list[FruitCreationPlacement]
) -> Image.Image:
    """Composite every transformed fruit into one transparent card layer.

    Args:
        fruit_svgs (list[str]): Source SVG per fruit.
        placements (list[FruitCreationPlacement]): Placement per fruit.

    Returns:
        Image.Image: Transparent RGBA foreground for the final PNG.
    """
    foreground = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT))
    for fruit_svg, placement in zip(fruit_svgs, placements):
        sprite = create_fruit_sprite(fruit_svg, placement)
        left = round(placement.center_x - sprite.width / 2)
        top = round(placement.center_y - sprite.height / 2)
        # alpha_composite needs a source box that stays on the card.
        source_left, source_top = max(0, -left), max(0, -top)
        right = min(CARD_WIDTH, left + sprite.width)
        bottom = min(CARD_HEIGHT, top + sprite.height)
        if right <= max(0, left) or bottom <= max(0, top):
            continue
        foreground.alpha_composite(
            sprite,
            dest=(max(0, left), max(0, top)),
            source=(
                source_left,
                source_top,
                source_left + right - max(0, left),
                source_top + bottom - max(0, top),
            ),
        )
    return foreground


@lru_cache(maxsize=1)
def get_card_mask() -> Image.Image:
    """Rasterize the card's outer curve once for both background and final PNG."""
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CARD_WIDTH}" '
        f'height="{CARD_HEIGHT}">'
        f'<rect width="{CARD_WIDTH}" height="{CARD_HEIGHT}" '
        f'rx="{CARD_CORNER_RADIUS}" fill="white" />'
        "</svg>"
    )
    png = cairosvg.svg2png(bytestring=svg.encode("utf-8"))
    return Image.open(BytesIO(png)).convert("RGBA").getchannel("A")
