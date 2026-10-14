import { AppTooltip } from "../tooltip/tooltip";
import { formatNumber } from "../../utils/number";
import type { BattleTowers, SideTowers, Tower } from "../../utils/towers";
import "./towerHealth.css";

function towerLabel(tower: Tower): string {
  if (tower.hp === null) return `${tower.name}: not recorded`;
  if (tower.hp === 0) return `${tower.name}: destroyed`;
  const hp = `${tower.name}: ${formatNumber(tower.hp)} HP left`;
  return tower.ratio === null ? `${hp} (full health unknown)` : hp;
}

function towerState(tower: Tower): string {
  if (tower.hp === null || tower.ratio === null) return " is-unknown";
  return tower.hp === 0 ? " is-destroyed" : "";
}

function TowerSet({
  towers,
  side,
}: Readonly<{ towers: SideTowers; side: "team" | "opponent" }>) {
  // King in the middle, like on the arena
  const row = [
    { slot: "left", tower: towers.crowns[0] },
    { slot: "king", tower: towers.king },
    { slot: "right", tower: towers.crowns[1] },
  ];
  const labels = row.map(({ tower }) => towerLabel(tower));
  const owner = side === "team" ? "Team" : "Opponent";
  // One tooltip for the three towers keeps a long battle list light. A button,
  // so the keyboard reaches it and its tooltip opens on focus.
  return (
    <AppTooltip
      tapToShow
      title={
        <>
          {row.map(({ slot }, i) => (
            <div key={slot}>{labels[i]}</div>
          ))}
        </>
      }
    >
      <button
        type="button"
        className={`tower-health-set is-${side}`}
        aria-label={`${owner} towers. ${labels.join(". ")}`}
      >
        {row.map(({ slot, tower }) => (
          <span
            key={slot}
            className={`tower-health-tower is-${tower.kind}${towerState(tower)}`}
          >
            {tower.ratio !== null && (
              <span
                className="tower-health-fill"
                style={{ height: `${Math.round(tower.ratio * 100)}%` }}
              />
            )}
          </span>
        ))}
      </button>
    </AppTooltip>
  );
}

/**
 * Both sides' remaining tower health, facing each other like on the arena.
 *
 * @param towers - The battle's towers (see getBattleTowers)
 */
export function TowerHealth({ towers }: Readonly<{ towers: BattleTowers }>) {
  return (
    <div className="tower-health">
      <TowerSet towers={towers.team} side="team" />
      <TowerSet towers={towers.opponent} side="opponent" />
    </div>
  );
}
