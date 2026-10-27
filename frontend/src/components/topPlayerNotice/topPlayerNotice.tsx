import { useId, useState } from "react";
import { Crown } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { trackPlayer } from "../../services/api/trackedPlayers";
import { getTrackingErrorStatus } from "../../utils/trackingErrors";
import { formatSeasonLabel } from "../../utils/seasons";
import { NoticeCard } from "../noticeCard/noticeCard";
import { StatusMessage } from "../statusMessage/statusMessage";
import type { Status } from "../../types/status";
import type { TopFinish } from "../../types/player";
import "./topPlayerNotice.css";

/**
 * Notice on the page of one of the last finished season's top players that
 * nobody keeps yet. Such a player is tracked for the meta analysis until the
 * next season and paused (parked) afterwards. Kept (regular) players get no
 * notice; the badge in the banner already marks their finish.
 *
 * "Keep tracking" goes through the add route like "Track again" on a parked
 * player, which for a tracked player only marks it regular. After a success
 * the notice only shows that status, so the layout keeps it mounted
 * (onKept) once the refetched profile reports the player as regular.
 *
 * @param playerTag - The player's tag as in the page URL
 * @param topFinish - Season and leaderboard rank
 * @param onKept - Called once the player is kept, before the refetch
 */
export function TopPlayerNotice({
  playerTag,
  topFinish,
  onKept,
}: Readonly<{
  playerTag: string;
  topFinish: TopFinish;
  onKept: () => void;
}>) {
  const queryClient = useQueryClient();
  const statusId = useId();
  const [pending, setPending] = useState(false);
  const [status, setStatus] = useState<Status | null>(null);

  const handleKeep = async () => {
    setPending(true);
    setStatus(null);
    try {
      await trackPlayer(playerTag);
      // Regular players are still parked near capacity, least viewed
      // first, so this promises no more than any other tracked player gets.
      setStatus({
        tone: "success",
        title: "Kept after this season",
        message: "Tracked like any other player once the next season starts.",
      });
      onKept();
      void queryClient.invalidateQueries({
        queryKey: ["playerProfile", playerTag],
      });
    } catch (e) {
      setStatus(getTrackingErrorStatus(e, "Couldn't keep tracking player"));
    } finally {
      setPending(false);
    }
  };

  if (status?.tone === "success")
    return (
      <StatusMessage
        status={status}
        id={statusId}
        className="top-player-notice-kept"
      />
    );

  return (
    <NoticeCard
      icon={Crown}
      tone="legend"
      title="Top player of last season"
      status={status}
      statusId={statusId}
      action={
        <button
          type="button"
          onClick={handleKeep}
          disabled={pending}
          aria-describedby={status ? statusId : undefined}
        >
          {pending ? "Keeping..." : "Keep tracking"}
        </button>
      }
    >
      Tracked only as #{topFinish.rank} in Path of Legend{" "}
      {formatSeasonLabel(topFinish.season)}, until the next season. Keep
      tracking the player to follow them beyond that.
    </NoticeCard>
  );
}
