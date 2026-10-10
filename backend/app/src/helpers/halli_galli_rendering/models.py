"""Immutable fruit styles, placements and server-only card metadata."""

import math

from pydantic import BaseModel, ConfigDict, Field

from .constants import (
    CARD_HEIGHT,
    CARD_WIDTH,
    FRUIT_SIZE,
)


class FruitStyle(BaseModel):
    """Random visual variation applied to one fruit icon."""

    # Variation is generated once per fruit, then reused during layout retries
    # so retries change positions without unexpectedly changing the artwork.
    model_config = ConfigDict(frozen=True)

    scale: float
    is_horizontally_flipped: bool
    rotation_degrees: float
    hue_shift: float
    saturation_multiplier: float
    lightness_multiplier: float


class FruitCreationPlacement(BaseModel):
    """Internal placement and styling used while generating a fruit image."""

    model_config = ConfigDict(frozen=True)

    relative_x: float = Field(ge=0, le=1)
    relative_y: float = Field(ge=0, le=1)
    style: FruitStyle

    @property
    def center_x(self) -> float:
        """Return the horizontal center in card pixels for rendering.

        Returns:
            float: Pixel coordinate measured from the left edge.
        """

        return self.relative_x * CARD_WIDTH

    @property
    def center_y(self) -> float:
        """Return the vertical center in card pixels for rendering.

        Returns:
            float: Pixel coordinate measured from the top edge.
        """

        return self.relative_y * CARD_HEIGHT

    def to_image_bounds(
        self, visible_hull: tuple[tuple[float, float], ...]
    ) -> "FruitImageBounds":
        """Transform the cached painted SVG hull into an unchecked image box.

        Args:
            visible_hull (tuple[tuple[float, float], ...]): Painted SVG outline
                in the source icon's pixel coordinates.

        Returns:
            FruitImageBounds: Normalized box before on-card checks.
        """

        image_x = self.center_x - FRUIT_SIZE / 2
        image_y = self.center_y - FRUIT_SIZE / 2
        horizontal_scale = (
            -self.style.scale
            if self.style.is_horizontally_flipped
            else self.style.scale
        )
        vertical_scale = self.style.scale
        rotation = math.radians(self.style.rotation_degrees)
        cosine = math.cos(rotation)
        sine = math.sin(rotation)

        transformed_points = []
        for source_x, source_y in visible_hull:
            # NOTE This matches the scale -> flip -> rotate around the center
            # that create_fruit_sprite in svg.py applies to each fruit. Change
            # both together, or click boxes drift from the drawn fruit.
            offset_x = (image_x + source_x - self.center_x) * horizontal_scale
            offset_y = (image_y + source_y - self.center_y) * vertical_scale
            transformed_points.append(
                (
                    self.center_x + offset_x * cosine - offset_y * sine,
                    self.center_y + offset_x * sine + offset_y * cosine,
                )
            )

        min_x = min(point[0] for point in transformed_points)
        max_x = max(point[0] for point in transformed_points)
        min_y = min(point[1] for point in transformed_points)
        max_y = max(point[1] for point in transformed_points)
        return FruitImageBounds(
            x=min_x / CARD_WIDTH,
            y=min_y / CARD_HEIGHT,
            width=(max_x - min_x) / CARD_WIDTH,
            height=(max_y - min_y) / CARD_HEIGHT,
        )

    def to_image_position(
        self, visible_hull: tuple[tuple[float, float], ...]
    ) -> "FruitImagePosition":
        """Return a validated final-image box for an accepted placement.

        Args:
            visible_hull (tuple[tuple[float, float], ...]): Painted SVG outline.

        Returns:
            FruitImagePosition: Normalized box kept for server-side click checks.
        """

        return FruitImagePosition(**self.to_image_bounds(visible_hull).model_dump())


class FruitImageBounds(BaseModel):
    """Internal normalized box that may temporarily extend outside the card."""

    model_config = ConfigDict(frozen=True)

    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class FruitImagePosition(BaseModel):
    """A normalized painted fruit bounding box on the final card image."""

    model_config = ConfigDict(frozen=True)

    # x/y identify the top-left corner in normalized 0-1 card coordinates.
    # Bounds derive from source SVG alpha pixels, not the full viewport, and
    # remain usable when the PNG is displayed at a different size.
    # Keep these values server-side for click hit testing: a submitted pointer
    # coordinate can be checked against the target fruit's painted bounds. This
    # means automation must locate the fruit on the image, not merely classify
    # the card's fruit type or count. Do not expose these boxes to the client.
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class HalliGalliCard(BaseModel):
    """A rendered card and the server-side metadata used to produce it."""

    # The renderer has finished when this model is created; immutable metadata
    # prevents its answer from drifting away from the rendered image in memory.
    model_config = ConfigDict(frozen=True)

    image: bytes
    fruit: str
    amount: int
    fruit_positions: list[FruitImagePosition]
