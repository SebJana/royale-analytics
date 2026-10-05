import "./statCard.css";
import Tooltip from "@mui/material/Tooltip";
import type { ReactNode } from "react";
import { formatNumber } from "../../utils/number";

export function StatCard({
  value,
  label,
  tooltip,
}: Readonly<{ value: string | number; label: string; tooltip?: ReactNode }>) {
  const card = (
    <div className="stat-card">
      {/* Numbers get thousands separators; strings (e.g. "54.6%") show as given */}
      <div className="stat-card-number">
        {typeof value === "number" ? formatNumber(value) : value}
      </div>
      <div className="stat-card-label">{label}</div>
    </div>
  );

  return tooltip ? (
    <Tooltip
      arrow
      placement="top"
      title={tooltip}
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
    </Tooltip>
  ) : (
    card
  );
}
