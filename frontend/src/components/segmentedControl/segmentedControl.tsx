import { useId } from "react";
import Tooltip from "@mui/material/Tooltip";
import "./segmentedControl.css";

export type SegmentedOption<T extends string> = {
  value: T;
  label: string;
  danger?: boolean; // Selected in red, like the excluded cards
};

type SegmentedControlProps<T extends string> = {
  options: SegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string; // Name of the group when no title shows
  title?: string; // Small uppercase heading above the segments
  hint?: Partial<Record<T, string>>; // Line below, for the selected option
  tooltip?: string; // Shown when hovering the segments
};

/**
 * A choice between a few equal options, shown side by side with the selected
 * one highlighted. Meant for either/or choices: an on/off switch would leave
 * open which side is active.
 *
 * The segments are native radio inputs, so screen readers announce the
 * position ("1 of 2") and the arrow keys move the selection.
 * All hints share one grid cell and only the selected one is visible, so the
 * control keeps the width of the longest hint and its row does not shift.
 */
export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  title,
  hint,
  tooltip,
}: Readonly<SegmentedControlProps<T>>) {
  // Radio inputs group by name, which has to be unique on the page
  const name = useId();
  const titleId = `${name}-title`;

  const group = (
    <div
      className="segmented-control-group"
      role="radiogroup"
      aria-labelledby={title ? titleId : undefined}
      aria-label={title ? undefined : ariaLabel}
    >
      {options.map((option) => {
        const isSelected = option.value === value;
        return (
          <label
            key={option.value}
            className={`segmented-control-option${isSelected ? " is-selected" : ""}${
              option.danger ? " is-danger" : ""
            }`}
          >
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={isSelected}
              onChange={() => onChange(option.value)}
              className="segmented-control-input"
            />
            {option.label}
          </label>
        );
      })}
    </div>
  );

  return (
    <div className="segmented-control">
      {title && (
        <span id={titleId} className="segmented-control-title">
          {title}
        </span>
      )}
      {tooltip ? (
        <Tooltip arrow title={tooltip}>
          {group}
        </Tooltip>
      ) : (
        group
      )}
      {hint && (
        <span className="segmented-control-hint">
          {options.map((option) => (
            <span
              key={option.value}
              className={option.value === value ? "" : "is-hidden"}
              aria-hidden={option.value !== value}
            >
              {hint[option.value]}
            </span>
          ))}
        </span>
      )}
    </div>
  );
}
