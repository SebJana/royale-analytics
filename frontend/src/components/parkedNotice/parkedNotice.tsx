import { useId, useState } from "react";
import { CirclePause } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { trackPlayer } from "../../services/api/trackedPlayers";
import { TRACKED_PLAYER_COUNT_QUERY_KEY } from "../../hooks/useTrackedPlayerCount";
import { getTrackingErrorStatus } from "../../utils/trackingErrors";
import { StatusMessage } from "../statusMessage/statusMessage";
import { NoticeCard } from "../noticeCard/noticeCard";
import type { Status } from "../../types/status";
import "./parkedNotice.css";

/**
 * Notice on the page of a parked player, with the button that tracks it
 * again. Parked players are taken off syncing when tracking nears capacity,
 * least viewed first, but keep their stored data and their page.
 *
 * Tracking again goes through the regular add route, which parks the next
 * least viewed player if the pool is full. Its outcome uses the same
 * StatusMessage as adding and removing on the home page. After a success
 * the notice only shows that status, so the layout keeps it mounted
 * (onResumed) once the refetched profile reports the player as active.
 *
 * @param playerTag - The player's tag as in the page URL
 * @param since - Day the pause started (YYYY-MM-DD), null if unknown
 * @param onResumed - Called once tracking resumed, before the refetch
 */
export function ParkedNotice({
  playerTag,
  since,
  onResumed,
}: Readonly<{
  playerTag: string;
  since?: string | null;
  onResumed: () => void;
}>) {
  const queryClient = useQueryClient();
  const statusId = useId();
  const [pending, setPending] = useState(false);
  const [status, setStatus] = useState<Status | null>(null);

  const handleTrackAgain = async () => {
    setPending(true);
    setStatus(null);
    try {
      const result = await trackPlayer(playerTag);
      // As on the home page, but without the "View Player" link: this is
      // the player's page.
      setStatus({ tone: "success", title: result.status, message: result.tag });
      onResumed();
      // The refetched profile reports the player as active. The battles are
      // read again too: a player parked before its first sync now waits for
      // that sync, which the page polls for.
      void queryClient.invalidateQueries({
        queryKey: ["playerProfile", playerTag],
      });
      void queryClient.invalidateQueries({
        queryKey: ["playerBattles", playerTag],
      });
      void queryClient.invalidateQueries({
        queryKey: [TRACKED_PLAYER_COUNT_QUERY_KEY],
      });
    } catch (e) {
      setStatus(getTrackingErrorStatus(e, "Couldn't track player again"));
    } finally {
      setPending(false);
    }
  };

  if (status?.tone === "success")
    return (
      <StatusMessage
        status={status}
        id={statusId}
        className="parked-notice-resumed"
      />
    );

  return (
    <NoticeCard
      icon={CirclePause}
      title="Tracking paused"
      status={status}
      statusId={statusId}
      action={
        <button
          type="button"
          onClick={handleTrackAgain}
          disabled={pending}
          aria-describedby={status ? statusId : undefined}
        >
          {pending ? "Tracking..." : "Track again"}
        </button>
      }
    >
      {since ? `Paused since ${since}` : "This player was paused"} to make room
      for players viewed more often. Its battles aren&apos;t checked anymore,
      only games against other tracked players are still recorded.
    </NoticeCard>
  );
}
