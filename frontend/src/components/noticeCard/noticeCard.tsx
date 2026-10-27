import { useId, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { StatusMessage } from "../statusMessage/statusMessage";
import type { Status } from "../../types/status";
import "./noticeCard.css";

/**
 * A card above the player banner that explains the player's tracking state
 * and offers the one action that changes it: "Track again" for a parked
 * player, "Keep tracking" for a top player. Shared, so both read as part of
 * the page in the same way.
 *
 * @param icon - Icon left of the title
 * @param tone - warning (orange icon) for a pause, legend (purple) for a
 *   top player
 * @param title - Short heading of the state
 * @param children - The explanation below the title
 * @param status - Outcome of the action, null to collapse its slot
 * @param statusId - Id of the outcome, for aria-describedby on the action
 * @param action - The button, if the state can be changed here
 */
export function NoticeCard({
  icon: Icon,
  tone = "warning",
  title,
  children,
  status = null,
  statusId,
  action,
}: Readonly<{
  icon: LucideIcon;
  tone?: "warning" | "legend";
  title: string;
  children: ReactNode;
  status?: Status | null;
  statusId?: string;
  action?: ReactNode;
}>) {
  const titleId = useId();
  return (
    <section
      className={`notice-card notice-card-${tone}`}
      aria-labelledby={titleId}
    >
      <Icon className="notice-card-icon" size={32} aria-hidden="true" />
      <h2 id={titleId} className="notice-card-title">
        {title}
      </h2>
      <p className="notice-card-message">{children}</p>
      <StatusMessage
        status={status}
        id={statusId}
        className="notice-card-status"
      />
      {action && <div className="notice-card-action">{action}</div>}
    </section>
  );
}
