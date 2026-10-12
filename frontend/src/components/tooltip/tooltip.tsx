import Tooltip, { type TooltipProps } from "@mui/material/Tooltip";
import ClickAwayListener from "@mui/material/ClickAwayListener";
import {
  cloneElement,
  useRef,
  useState,
  type MouseEvent,
  type PointerEvent,
  type ReactElement,
} from "react";

type AnchorProps = {
  onClick?: (event: MouseEvent<HTMLElement>) => void;
  onPointerDown?: (event: PointerEvent<HTMLElement>) => void;
};

/**
 * The site's tooltip. Its look comes from the MUI theme (main.tsx, see
 * TOOLTIP_STYLE), so every tooltip shares it.
 *
 * Touch screens have no hover. On buttons and links a tap keeps doing their
 * action, and a long press shows the tooltip as MUI does. For an element
 * that only explains something (an info icon, a status badge, a cut off
 * title), `tapToShow` makes a tap open and close the tooltip instead, and a
 * tap elsewhere closes it.
 *
 * @param props - MUI Tooltip props; `arrow` is on unless turned off
 * @param props.tapToShow - Toggle the tooltip with a tap on touch screens
 */
export function AppTooltip({
  children,
  tapToShow = false,
  arrow = true,
  ...props
}: Readonly<
  Omit<TooltipProps, "children"> & {
    children: ReactElement<AnchorProps>;
    tapToShow?: boolean;
  }
>) {
  const [open, setOpen] = useState(false);
  // The pointer of the last press, since a click event does not tell touch
  // from mouse
  const pointerType = useRef<string>("mouse");

  if (!tapToShow) {
    return (
      <Tooltip arrow={arrow} {...props}>
        {children}
      </Tooltip>
    );
  }

  const anchor = cloneElement(children, {
    onPointerDown: (event: PointerEvent<HTMLElement>) => {
      pointerType.current = event.pointerType;
      children.props.onPointerDown?.(event);
    },
    onClick: (event: MouseEvent<HTMLElement>) => {
      // A mouse already opened it on hover, a click must not close it again
      if (pointerType.current === "touch") setOpen((isOpen) => !isOpen);
      children.props.onClick?.(event);
    },
  });

  return (
    <ClickAwayListener onClickAway={() => setOpen(false)}>
      <Tooltip
        arrow={arrow}
        {...props}
        open={open}
        onOpen={() => setOpen(true)}
        onClose={() => setOpen(false)}
        // The tap above replaces MUI's long press, which a quick tap cancels
        disableTouchListener
      >
        {anchor}
      </Tooltip>
    </ClickAwayListener>
  );
}
