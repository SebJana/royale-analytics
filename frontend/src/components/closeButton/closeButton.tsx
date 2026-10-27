import type { ComponentPropsWithRef } from "react";
import { X } from "lucide-react";
import "./closeButton.css";

/**
 * The site's one X button, for closing a dialog and for removing an entry
 * from a list, so both look and behave the same.
 *
 * Every prop reaches the button, including the ref and handlers a tooltip
 * attaches, so it works inside AppTooltip. Pass an aria-label naming what it
 * closes or removes; the icon itself is hidden from screen readers. A
 * className is for placement only (e.g. a negative margin), not for the look.
 *
 * @param props - Props of the underlying button
 */
export function CloseButton({
  className,
  ...props
}: Readonly<ComponentPropsWithRef<"button">>) {
  return (
    <button
      type="button"
      {...props}
      className={`close-button${className ? ` ${className}` : ""}`}
    >
      <X aria-hidden="true" />
    </button>
  );
}
