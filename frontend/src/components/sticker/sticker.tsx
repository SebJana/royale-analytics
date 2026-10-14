import type { ComponentPropsWithRef } from "react";
import "./sticker.css";

export type StickerTone = "orange" | "gold" | "red";

/**
 * A solid label for something a player earned or that stands out:
 * a streak, a top rank, a three crown win. Rare by design; on every row it
 * would stop meaning anything.
 *
 * Children are plain text, or a `sticker-figure` (big number) next to a
 * `sticker-caption` (small stacked text).
 *
 * @param tone - gold for the very top, orange for notable, red for bad runs
 * @param size - sm for inline use in rows and headers
 */
export function Sticker({
  tone = "orange",
  size = "md",
  className,
  children,
  ...props
}: Readonly<
  ComponentPropsWithRef<"span"> & { tone?: StickerTone; size?: "sm" | "md" }
>) {
  return (
    <span
      {...props}
      className={`sticker sticker-${tone} sticker-${size}${
        className ? ` ${className}` : ""
      }`}
    >
      {children}
    </span>
  );
}
