import { useState } from "react";
import { Link } from "react-router";
import { AlertCircle, ArrowRight, CircleCheck, Clock } from "lucide-react";
import type { Status } from "../../types/status";
import "./statusMessage.css";

const STATUS_ICONS = {
  success: CircleCheck,
  error: AlertCircle,
  wait: Clock,
} as const;

/**
 * Outcome callout under an action: adding, removing and verifying on the
 * home page, tracking a parked player again on the player page. Shared, so
 * every outcome reads the same way.
 *
 * The slot stays mounted and opens or collapses its height, so the content
 * below slides instead of jumping when a status comes or goes.
 *
 * @param status - The outcome to show, null to collapse
 * @param id - Id of the callout, for aria-describedby on the action
 * @param className - Extra class of the slot, for its width and spacing
 */
export function StatusMessage({
  status,
  id,
  className,
}: Readonly<{ status: Status | null; id?: string; className?: string }>) {
  // Keeps the last status rendered while the slot collapses; dropping it
  // right away would empty the box before it finishes closing.
  const [shown, setShown] = useState(status);
  if (status && status !== shown) setShown(status);
  const open = status !== null;
  const Icon = shown ? STATUS_ICONS[shown.tone] : null;

  return (
    <div
      className={`status-slot${open ? " is-open" : ""}${
        className ? ` ${className}` : ""
      }`}
      aria-hidden={!open}
      inert={!open}
    >
      <div className="status-clip">
        {shown && Icon && (
          <div
            id={id}
            className={`status-message is-${shown.tone}`}
            role={shown.tone === "success" ? "status" : "alert"}
          >
            <Icon className="status-icon" size={20} aria-hidden="true" />
            <div className="status-text">
              <strong>{shown.title}</strong>
              <span>{shown.message}</span>
              {shown.link && (
                <Link to={shown.link.to} className="status-link">
                  {shown.link.label}
                  <ArrowRight size={18} aria-hidden="true" />
                </Link>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
