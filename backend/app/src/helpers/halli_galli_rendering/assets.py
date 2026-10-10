"""Source SVG discovery and cached hulls and palette extraction."""

import colorsys
import random
from collections import Counter
from io import BytesIO
from pathlib import Path

import cairosvg
from PIL import Image

from .constants import (
    ColorPalette,
    FRUIT_SIZE,
    RGBColor,
)

# Source assets and their painted hulls do not change while the application is
# running, so retain them directly in process memory after their first use to avoid
# unnecessary file read and calculation workload
FRUIT_SVG_CACHE: dict[str, tuple[str, ...]] = {}


VISIBLE_SVG_HULL_CACHE: dict[str, tuple[tuple[float, float], ...]] = {}


FRUIT_COLOR_CACHE: dict[str, ColorPalette] = {}


FRUIT_MAIN_COLOR_CACHE: dict[str, RGBColor] = {}


DOCKER_HALLI_GALLI_DIR = Path("/app/shared_resources/halli_galli")


def find_halli_galli_asset_dir() -> Path:
    """Find assets in Docker first, then locate them from a local checkout.

    Returns:
        Path: Directory containing the source fruit SVG folders.
    """

    if DOCKER_HALLI_GALLI_DIR.is_dir():
        # Docker copies shared resources to this stable runtime location.
        return DOCKER_HALLI_GALLI_DIR

    # Local paths vary by developer, so discover the repository resource folder
    # from this module rather than relying on a machine-specific absolute path.
    for parent in Path(__file__).resolve().parents:
        asset_dir = parent / "shared_resources" / "halli_galli"
        if asset_dir.is_dir():
            return asset_dir

    raise FileNotFoundError("Could not locate shared_resources/halli_galli")


def get_fruit_svgs(fruit: str) -> tuple[str, ...]:
    """Load and retain every SVG variation for one fruit in process memory.

    Args:
        fruit (str): Fruit folder to load.

    Returns:
        tuple[str, ...]: SVG source strings for that fruit.
    """

    cached_svgs = FRUIT_SVG_CACHE.get(fruit)
    if cached_svgs is not None:
        return cached_svgs

    fruit_path = find_halli_galli_asset_dir() / fruit
    svg_files = sorted(fruit_path.glob("*.svg"))

    if not svg_files:
        raise FileNotFoundError(f"No SVG files found in {fruit_path}")

    svg_contents = tuple(
        file_path.read_text(encoding="utf-8") for file_path in svg_files
    )
    FRUIT_SVG_CACHE[fruit] = svg_contents
    return svg_contents


def load_random_fruit_svg(fruit: str) -> str:
    """Select a random SVG variation from the cached source assets.

    Args:
        fruit (str): Fruit whose source artwork is needed.

    Returns:
        str: One SVG variation for this fruit.
    """

    return random.choice(get_fruit_svgs(fruit))


def _convex_hull(
    points: set[tuple[float, float]],
) -> tuple[tuple[float, float], ...]:
    """Return the convex hull used for fast affine bounding-box transforms.

    Args:
        points (set[tuple[float, float]]): Corners of painted source pixels.

    Returns:
        tuple[tuple[float, float], ...]: Outer points in hull order.
    """

    sorted_points = sorted(points)
    if len(sorted_points) <= 1:
        return tuple(sorted_points)

    def cross(
        origin: tuple[float, float],
        first: tuple[float, float],
        second: tuple[float, float],
    ) -> float:
        """Measure which side of an edge the next hull point lies on.

        Args:
            origin (tuple[float, float]): Edge starting point.
            first (tuple[float, float]): Edge ending point.
            second (tuple[float, float]): Candidate next point.

        Returns:
            float: Signed turn value used to remove inner points.
        """
        return (first[0] - origin[0]) * (second[1] - origin[1]) - (
            first[1] - origin[1]
        ) * (second[0] - origin[0])

    lower = []
    for point in sorted_points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)

    upper = []
    for point in reversed(sorted_points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)

    return tuple(lower[:-1] + upper[:-1])


def get_visible_svg_hull(svg: str) -> tuple[tuple[float, float], ...]:
    """Cache the convex hull of a fixed SVG's non-transparent source pixels.

    Args:
        svg (str): Source icon before scaling, rotation, or recoloring.

    Returns:
        tuple[tuple[float, float], ...]: Painted outline used to compute the
            actual click box after the icon is transformed.
    """

    cached_hull = VISIBLE_SVG_HULL_CACHE.get(svg)
    if cached_hull is not None:
        return cached_hull

    # Rendering source assets at their displayed 96px viewport captures their
    # real paths, fills, strokes, clipping, and transparent padding at once.
    png = cairosvg.svg2png(
        bytestring=svg.encode("utf-8"),
        output_width=FRUIT_SIZE,
        output_height=FRUIT_SIZE,
    )
    alpha = Image.open(BytesIO(png)).convert("RGBA").getchannel("A")
    pixel_corners = set()

    for index, opacity in enumerate(alpha.getdata()):
        if opacity == 0:
            continue

        pixel_x = index % FRUIT_SIZE
        pixel_y = index // FRUIT_SIZE
        # Pixel corners, rather than centers, avoid under-reporting the visible
        # extent by half a pixel before scale and rotation are applied.
        pixel_corners.update(
            {
                (pixel_x, pixel_y),
                (pixel_x + 1, pixel_y),
                (pixel_x, pixel_y + 1),
                (pixel_x + 1, pixel_y + 1),
            }
        )

    if not pixel_corners:
        raise ValueError("Fruit SVG has no visible pixels")

    hull = _convex_hull(pixel_corners)
    VISIBLE_SVG_HULL_CACHE[svg] = hull
    return hull


def get_fruit_colors(fruit: str) -> ColorPalette:
    """Sample vivid painted pixels from every base SVG for a fruit type."""
    if fruit in FRUIT_COLOR_CACHE:
        return FRUIT_COLOR_CACHE[fruit]

    colors = []
    for svg in get_fruit_svgs(fruit):
        # 64px is enough to sample the fills cheaply; every fourth pixel keeps
        # the cached palette small while retaining common colors many times.
        png = cairosvg.svg2png(
            bytestring=svg.encode("utf-8"), output_width=64, output_height=64
        )
        for pixel_index, (red, green, blue, alpha) in enumerate(
            Image.open(BytesIO(png)).convert("RGBA").getdata()
        ):
            # Ignore low-alpha edge blends so paper does not enter the palette.
            if pixel_index % 4 or alpha < 200:
                continue
            _, saturation, value = colorsys.rgb_to_hsv(
                red / 255, green / 255, blue / 255
            )
            # These thresholds keep vivid fills and leave near-gray or very
            # dark strokes out of the colors used for histogram balancing.
            if saturation >= 0.35 and value >= 0.25:
                # Keep repeated colors so common fills are sampled more often.
                colors.append((red, green, blue))
    if not colors:
        raise ValueError(f"No vivid painted colors found for {fruit}")
    FRUIT_COLOR_CACHE[fruit] = tuple(colors)
    return FRUIT_COLOR_CACHE[fruit]


def get_main_fruit_color(fruit: str) -> RGBColor:
    """Return the most common vivid, coarsely quantized source pixel color."""
    if fruit in FRUIT_MAIN_COLOR_CACHE:
        return FRUIT_MAIN_COLOR_CACHE[fruit]
    # Group channels into 16-value buckets, represented by their midpoint (+8).
    # Nearby shades then share one vote instead of splitting the dominant fill.
    color_counts = Counter(
        tuple(min(255, (channel // 16) * 16 + 8) for channel in color)
        for color in get_fruit_colors(fruit)
    )
    FRUIT_MAIN_COLOR_CACHE[fruit] = color_counts.most_common(1)[0][0]
    return FRUIT_MAIN_COLOR_CACHE[fruit]
