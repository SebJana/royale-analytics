import "./statCard.css";
import { AppTooltip } from "../tooltip/tooltip";
import type { ReactNode } from "react";
import { formatNumber } from "../../utils/number";

export function StatCard({
  value,
  label,
  tooltip,
  className,
}: Readonly<{
  value: ReactNode;
  label: string;
  tooltip?: ReactNode;
  className?: string;
}>) {
  const card = (
    <div className={className ? `stat-card ${className}` : "stat-card"}>
      {/* Numbers get thousands separators; strings (e.g. "54.6%") show as given */}
      <div className="stat-card-number">
        {typeof value === "number" ? formatNumber(value) : value}
      </div>
      <div className="stat-card-label">{label}</div>
    </div>
  );

  return tooltip ? (
    <AppTooltip
      placement="top"
      title={tooltip}
      // The card only explains a number, so a tap shows the explanation
      tapToShow
      slotProps={{
        popper: {
          modifiers: [
            // Keep the tooltip centered over the stat and let it overlap slightly.
            { name: "offset", options: { offset: [0, -18] } },
            { name: "flip", options: { fallbackPlacements: ["bottom"] } },
          ],
        },
      }}
    >
      {card}
    </AppTooltip>
  ) : (
    card
  );
}
