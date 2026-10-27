"""Tuning constants and shared types for Fruit Buzz card generation.

Kept apart from ``helpers.fruit_buzz_card`` so the stage modules and the
generation API import each other at the top of the file without a cycle.
"""

RGBColor = tuple[int, int, int]

ColorPalette = tuple[RGBColor, ...]

# Keep fruit details readable while varying layout, artwork, and background hues.

# Portrait card at a 1.4:1 height/width ratio. Keep the image small enough to
# generate quickly while retaining recognizable emoji details at display size.
CARD_WIDTH = 320

CARD_HEIGHT = 448

# Base SVG viewport; the per-count scales below set the displayed fruit size.
FRUIT_SIZE = 96

# Each fruit SVG is rasterized once at this multiple of FRUIT_SIZE, then
# scaled, recolored and rotated per card. Twice the viewport keeps edges sharp
# up to the largest 1.35 scale.
FRUIT_SPRITE_SUPERSAMPLE = 2

# Fewer fruits have more card space, so they can be noticeably larger.
FRUIT_SCALE_RANGES = {
    1: (1.10, 1.35),
    2: (1.00, 1.25),
    3: (0.90, 1.18),
    4: (0.82, 1.04),
    5: (0.75, 0.93),
}

# Dense cards use smaller rotation bounds so all randomized layouts fit.
FRUIT_ROTATION_RANGES = {
    1: (-28, 28),
    2: (-24, 24),
    3: (-20, 20),
    4: (-14, 14),
    5: (-10, 10),
}

# Sparse cards can use stronger position variation; vertical movement is
# deliberately larger than horizontal movement on every card type.
FRUIT_POSITION_JITTER = {
    1: (32, 56),
    2: (32, 48),
    3: (28, 40),
    4: (22, 32),
    5: (18, 26),
}

# Shift the base formation as a whole before independently jittering fruits.
FORMATION_POSITION_SHIFT = {
    1: (30, 42),
    2: (28, 38),
    3: (22, 32),
    4: (18, 26),
    5: (14, 20),
}

# Eight pixels between painted bounds keeps adjacent fruits countable.
FRUIT_GAP = 8

# Retry individual positions, then the whole formation. Bound both loops so
# crowded layouts cannot hold up generation indefinitely.
FRUIT_PLACEMENT_ATTEMPTS = 100

FRUIT_LAYOUT_ATTEMPTS = 50

# The same 16px curve clips the PNG and reserves corner space during placement.
# There is no drawn card stroke; alpha defines the smooth outer edge.
CARD_CORNER_RADIUS = 16

# zlib level 1 encodes several times faster than the default 6 for ~9% larger
# PNGs. Lossy formats would blur the noise the card relies on.
CARD_PNG_COMPRESS_LEVEL = 1

# Light paper keeps the emoji visible. Independent RGB values give cards a
# slight tint without adding another strong fruit-colored background peak.
BACKGROUND_COLOR_RANGE = (235, 253)

# Mild grayscale texture breaks up flat paper without covering fruit details.
BACKGROUND_NOISE_STANDARD_DEVIATION = 4

# Longer walks with broad brushes cluster balancing pixels into fewer patches.
# Keep the hue budgets below unchanged when tuning the size of small marks.
BACKGROUND_BLOB_STEPS = 48

BACKGROUND_BLOB_RADIUS = 19

# Roughly one in three walks is broader. Mixing sizes avoids one uniform speck
# size and adds irregular connected background regions for component detectors.
BACKGROUND_LARGE_BLOB_PROBABILITY = 0.35

BACKGROUND_LARGE_BLOB_STEPS = 90

BACKGROUND_LARGE_BLOB_RADIUS = 29

# Walk shapes cards stamp their balancing clusters from (see background.py).
# Placement and colors stay random per card; the shapes recur. The first card
# builds the library (~0.5 s).
BACKGROUND_WALK_LIBRARY_SIZE = 900

# Replace this many shapes every this many cards (~20 ms on that card), so a
# recurring shape is gone before many cards show it: the whole library turns
# over every ~450 cards.
BACKGROUND_WALK_REFRESH_CARDS = 20

BACKGROUND_WALK_REFRESH_COUNT = 40

# Paper textures reused across cards, flipped and tinted per card.
BACKGROUND_PAPER_TILES = 8

# Ten-degree hue bins cover the whole hue circle, including red across 0/1.
BACKGROUND_HUE_BINS = 36

# Approximate vivid-pixel budgets for the dominant SVG fills: red at 0/34/35,
# orange/yellow at 2/3/4, and purple across 24-28. Unequal budgets reflect the source
# palette and painted areas, rather than giving every hue the same pixel count.
# Actual fruit and textured decoy pixels are deducted before filling deficits.
# These are balancing targets, not a guarantee of identical card histograms.
# Cover shifted purple shades too, so recoloring does not reveal a grape card.
# Scale every shared target together to prioritize clear paper over dense noise.
BACKGROUND_HUE_BUDGET_SCALE = 0.65

BACKGROUND_HUE_TARGETS = {
    0: 4500,
    2: 7000,
    3: 8500,
    4: 7000,
    24: 1200,
    25: 4250,
    26: 5500,
    27: 4250,
    28: 1200,
    34: 5500,
    35: 6000,
}

# Neighboring hue bins need fewer pixels; this broadens the main peaks without
# spending most of the card on colors that rarely occur in the source artwork.
BACKGROUND_SIDE_HUE_TARGET = 450

# Count clusters within 5px as contacts. Reject excess clusters whole rather
# than scattering skipped pixels into a distinctive sparse fringe around fruit.
BACKGROUND_FRUIT_CONTACT_WIDTH = 11

BACKGROUND_FRUIT_CONTACT_LIMIT = (1, 2)

# Do not finish a hue budget with a tiny fragment of a larger cluster.
BACKGROUND_MIN_CLUSTER_PIXELS = 24

# A 35px max filter reserves 17px around painted fruit for big decoys only.
# Small balancing marks can still occupy that space, avoiding a visible halo.
BACKGROUND_DECOY_CLEARANCE_WIDTH = 35

# Zero through five rival-colored decoys, sampled independently of fruit count.
# Placement can reject shapes that would crowd the real fruit.
BACKGROUND_DECOY_COUNT = (0, 5)

# A 100px mask produces lobes comparable to a small fruit instance. Shapes are
# deliberately irregular so a person can distinguish them from fruit emojis.
BACKGROUND_DECOY_SIZE = 100

# Vary each blob's brightness, retaining its source hue. Separate shades make
# multiple decoys less like copies of one flat color patch.
BACKGROUND_DECOY_SHADES = (0.78, 0.90, 1.02, 1.14)

# Limit normal retries to keep generation bounded. Rejected decoys get a smaller
# 70px retry with the same clearance so rival palettes survive crowded layouts.
BACKGROUND_DECOY_PLACEMENT_ATTEMPTS = 70

BACKGROUND_DECOY_FALLBACK_ATTEMPTS = 200

BACKGROUND_DECOY_FALLBACK_SIZE = 70

# Small flecks and a few short streaks alter the painted surface while leaving
# the fruit silhouette and distinguishing emoji details available to the viewer.
FRUIT_ARTIFACT_DOTS = 210

FRUIT_ARTIFACT_STREAKS = 4

# Broad translucent discoloration patches cover at most 30% of each fruit's
# opaque surface. Keep 1-2 patches and the same alpha so marks grow in area
# while the fruit silhouette and details remain visible underneath.
FRUIT_ARTIFACT_PATCHES = (1, 2)

FRUIT_ARTIFACT_PATCH_COVERAGE = 0.30

FRUIT_ARTIFACT_PATCH_ALPHA = 150

FRUIT_ARTIFACT_PATCH_ATTEMPTS = 20

# Neutral marks span both paper and fruit without adding a fruit-specific hue.
# Hollow rings and broad squiggly strokes leave most of the image unobstructed.
CARD_ARTIFACT_RINGS = (2, 4)

CARD_ARTIFACT_LINES = (2, 3)

CARD_ARTIFACT_LINE_WIDTH = (6, 9)

CARD_ARTIFACT_LINE_AMPLITUDE = (14, 28)

CARD_ARTIFACT_LINE_CYCLES = (2.5, 4.0)

CARD_ARTIFACT_COVERAGE = 0.07

CARD_ARTIFACT_FRUIT_COVERAGE = 0.10

CARD_ARTIFACT_ATTEMPTS = 16

AVAILABLE_FRUITS = ("banana", "grapes", "orange", "strawberry")

# Relative center-coordinates in [0.0, 1.0] for the fruit items
# (x, y) measured from top-left, based on how many elements are on the card
# Spread the centers over the portrait area, leaving room for scaling, rotation,
# and background decoys. Random shifts/jitter keep these from being fixed targets.
FRUIT_POSITIONS = {
    1: [
        (0.50, 0.50),
    ],
    2: [
        (0.50, 0.33),
        (0.50, 0.67),
    ],
    3: [
        (0.50, 0.25),
        (0.32, 0.65),
        (0.68, 0.65),
    ],
    4: [
        (0.32, 0.32),
        (0.68, 0.32),
        (0.32, 0.68),
        (0.68, 0.68),
    ],
    5: [
        (0.30, 0.28),
        (0.70, 0.28),
        (0.50, 0.50),
        (0.30, 0.72),
        (0.70, 0.72),
    ],
}
