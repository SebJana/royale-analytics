import { useEffect, useRef, useState } from "react";
import type { PointerEvent } from "react";
import { Link } from "react-router-dom";
import { UserCheck, UserPlus } from "lucide-react";
import { CopyableTag } from "../copyableTag/copyableTag";
import { useTrackedState } from "../../hooks/useTrackedState";
import { addPlayerPath } from "../../utils/playerTag";
import "./battlePlayerInfo.css";

// A mouse has to rest this long on a player before the tracked state is
// asked, so sweeping across the battle list sends no request per player.
const HOVER_INTENT_MS = 150;

/**
 * Name and tag of a player in a battle; the name links to the player's page
 * once hovering shows they are tracked, and an icon offers to add them
 * otherwise.
 *
 * The lookup goes by tag, whether the name or the tag is hovered: names are
 * not unique, and a tag is one dict access in the API. Touch has no hover, so
 * a tap looks up at once, and so do a click on the name and keyboard focus on
 * the tag. Clicking the tag still only copies it. Every battle shares the
 * cached answer per tag, so a player looked up once shows the link in all
 * their battles without asking again (see useTrackedState).
 *
 * @param name - The name as played; null for a missing one
 * @param tag - The tag; without one, nothing is looked up
 * @param fallbackName - Shown when the name is missing
 * @param className - Classes for the block's layout
 * @param nameClassName - Classes for the name heading
 * @param tagClassName - Classes for the tag's text
 */
export function BattlePlayerInfo({
  name,
  tag,
  fallbackName,
  className,
  nameClassName,
  tagClassName,
}: Readonly<{
  name?: string | null;
  tag?: string | null;
  fallbackName: string;
  className?: string;
  nameClassName?: string;
  tagClassName?: string;
}>) {
  const [interested, setInterested] = useState(false);
  const timer = useRef<number | null>(null);
  const { data, isError, refetch } = useTrackedState(tag ?? "", interested);

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  const lookUp = () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
    if (!tag) return;
    // A failed lookup stays failed until asked again; a new hover is that.
    if (isError) void refetch();
    setInterested(true);
  };

  const onPointerEnter = (event: PointerEvent) => {
    if (event.pointerType !== "mouse") {
      lookUp();
      return;
    }
    timer.current = window.setTimeout(lookUp, HOVER_INTENT_MS);
  };

  const onPointerLeave = () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
  };

  const shownName = name ?? fallbackName;
  // Untracked players have nothing behind the name; the add icon is the action.
  const actionable = !!tag && !(data && !data.tracked);

  return (
    <div
      className={className}
      onPointerEnter={onPointerEnter}
      onPointerLeave={onPointerLeave}
      onFocus={lookUp}
    >
      <h3
        className={`battle-player-info-name${actionable ? " is-actionable" : ""}${
          nameClassName ? ` ${nameClassName}` : ""
        }`}
        onClick={lookUp}
      >
        {data?.tracked ? (
          <Link
            to={`/player/${encodeURIComponent(data.tag)}/battles`}
            className="battle-player-info-link"
            title="Tracked: show this player's battles"
          >
            {shownName}
            <UserCheck className="battle-player-info-icon" aria-hidden="true" />
            <span className="battle-player-info-hint">(tracked)</span>
          </Link>
        ) : (
          <>
            {shownName}
            {data && !data.tracked && (
              // Only fills in the add form: adding spends a Clash Royale
              // request and is limited per client, so it stays a choice.
              <Link
                to={addPlayerPath(data.tag)}
                className="battle-player-info-add"
                title="Not tracked: add this player"
              >
                <UserPlus
                  className="battle-player-info-icon"
                  aria-hidden="true"
                />
                <span className="battle-player-info-hint">
                  Not tracked, add this player
                </span>
              </Link>
            )}
          </>
        )}
      </h3>
      {tag && <CopyableTag tag={tag} className={tagClassName} />}
    </div>
  );
}
