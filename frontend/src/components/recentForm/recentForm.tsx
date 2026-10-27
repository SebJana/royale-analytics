import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent } from "react";
import { AppTooltip } from "../tooltip/tooltip";
import { Sticker } from "../sticker/sticker";
import type { Battle } from "../../types/lastBattles";
import { datetimeToLocale, formatTimeAgo } from "../../utils/datetime";
import { mapInternalNameToDisplayName } from "../../utils/gameModes";
import "./recentForm.css";

type Result = Battle["gameResult"];

const RESULT_LETTER: Record<Result, string> = {
  Victory: "W",
  Defeat: "L",
  Draw: "D",
};

// A run of wins or losses this long is called out next to the record
const STREAK_MIN = 5;

/**
 * What a bar's tooltip and label say about its battle.
 */
function describeBattle(battle: Battle) {
  return {
    score: `${battle.team[0].crowns}-${battle.opponent[0].crowns}`,
    mode: mapInternalNameToDisplayName(battle.gameMode),
    when:
      formatTimeAgo(battle.battleTime) ?? datetimeToLocale(battle.battleTime),
  };
}

/**
 * The tooltip of a bar: result and score, game mode, and when, on one line.
 * Three lines would not fit above the pinned strip while scrubbing, and MUI
 * would flip the tooltip below it, under the finger. The bars use the same
 * line, so both read alike.
 */
function BattleTooltipText({ battle }: Readonly<{ battle: Battle }>) {
  const { score, mode, when } = describeBattle(battle);
  return (
    <span className="recent-form-tooltip-text">
      {battle.gameResult} {score} · {mode} · {when}
    </span>
  );
}

// Lets the one line run past MUI's default tooltip width instead of wrapping
const ONE_LINE_TOOLTIP = { tooltip: { sx: { maxWidth: "none" } } };

/**
 * The bars of a run of battles, wins above the line and losses below it.
 * The strip is one tab stop; the arrow keys move between its battles.
 *
 * With `scrub`, a finger on the strip picks the battle under it as it moves,
 * shown by the marker and a tooltip, and lifting it jumps there. The whole
 * strip is one target that way: 20 bars on a phone are too narrow to hit
 * one by one.
 *
 * @param timeline - Oldest first, the order the bars are drawn in
 * @param onSelect - Called with a clicked battle's index in `timeline`
 * @param label - Names the strip, e.g. to tell the pinned copy apart
 * @param current - Index in `timeline` of the battle being read, if any
 * @param scrub - Pick the battle by dragging on touch screens
 */
function FormStrip({
  timeline,
  onSelect,
  label,
  current,
  scrub = false,
}: Readonly<{
  timeline: Battle[];
  onSelect?: (index: number) => void;
  label: string;
  current?: number | null;
  scrub?: boolean;
}>) {
  // Starts on the newest, the last bar
  const [active, setActive] = useState<number | null>(null);
  const tickRefs = useRef<(HTMLButtonElement | null)[]>([]);
  // The bar under the finger while scrubbing
  const [scrubbed, setScrubbed] = useState<number | null>(null);
  const scrubbing = useRef(false);
  // The click the lift of a scrub may still fire on a bar, which must not
  // jump a second time
  const ignoreClick = useRef(false);

  const sequence = timeline.map((b) => RESULT_LETTER[b.gameResult]).join(" ");
  const focused = Math.min(active ?? timeline.length - 1, timeline.length - 1);
  const marked = scrubbed ?? current;

  const moveFocus = (event: KeyboardEvent<HTMLOListElement>) => {
    const next = {
      ArrowLeft: focused - 1,
      ArrowRight: focused + 1,
      Home: 0,
      End: timeline.length - 1,
    }[event.key];
    if (next === undefined || next < 0 || next >= timeline.length) return;
    event.preventDefault();
    setActive(next);
    tickRefs.current[next]?.focus();
  };

  // The columns are equally wide and meet, so the position alone tells the
  // bar, also past either end
  const barAt = (event: PointerEvent<HTMLOListElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const share = (event.clientX - rect.left) / rect.width;
    return Math.min(
      Math.max(Math.floor(share * timeline.length), 0),
      timeline.length - 1,
    );
  };

  const endScrub = () => {
    scrubbing.current = false;
    setScrubbed(null);
  };

  const scrubHandlers = scrub &&
    onSelect && {
      onPointerDown: (event: PointerEvent<HTMLOListElement>) => {
        // A mouse has hover and exact clicks already
        if (event.pointerType === "mouse") return;
        event.currentTarget.setPointerCapture(event.pointerId);
        scrubbing.current = true;
        setScrubbed(barAt(event));
      },
      onPointerMove: (event: PointerEvent<HTMLOListElement>) => {
        if (scrubbing.current) setScrubbed(barAt(event));
      },
      onPointerUp: (event: PointerEvent<HTMLOListElement>) => {
        if (!scrubbing.current) return;
        endScrub();
        ignoreClick.current = true;
        setTimeout(() => (ignoreClick.current = false));
        onSelect(barAt(event));
      },
      // A vertical drag scrolls the page instead (touch-action, recentForm.css)
      onPointerCancel: endScrub,
    };

  return (
    <>
      <ol
        className={`recent-form-strip${onSelect ? " is-selectable" : ""}${
          marked != null ? " has-current" : ""
        }`}
        aria-label={`${label} from oldest to newest: ${sequence}. Use the arrow keys for each battle${
          onSelect ? ", Enter to jump to it" : ""
        }.`}
        onKeyDown={moveFocus}
        {...scrubHandlers}
      >
        {timeline.map((battle, i) => {
          const { score, mode, when } = describeBattle(battle);
          return (
            <li key={`${battle.battleTime}-${i}`}>
              {/* A tap jumps to the battle like a click, so the tooltip is
                  left to a long press (see AppTooltip), or to the scrub */}
              <AppTooltip
                disableTouchListener={scrub}
                slotProps={ONE_LINE_TOOLTIP}
                title={<BattleTooltipText battle={battle} />}
              >
                <button
                  type="button"
                  ref={(element) => {
                    tickRefs.current[i] = element;
                  }}
                  className={`recent-form-tick is-${battle.gameResult.toLowerCase()}${
                    i === marked ? " is-current" : ""
                  }`}
                  aria-current={i === marked || undefined}
                  tabIndex={i === focused ? 0 : -1}
                  aria-label={`${battle.gameResult} ${score}, ${mode}, ${when}`}
                  onFocus={() => setActive(i)}
                  onClick={
                    onSelect &&
                    (() => {
                      if (!ignoreClick.current) onSelect(i);
                    })
                  }
                />
              </AppTooltip>
            </li>
          );
        })}
      </ol>
      {scrub && (
        // One tooltip that follows the finger from bar to bar, instead of
        // each bar opening its own. Above, where the finger does not cover
        // it; portaled, so it shows over the navigation.
        <AppTooltip
          open={scrubbed !== null}
          placement="top"
          disableHoverListener
          disableFocusListener
          disableTouchListener
          slotProps={{
            ...ONE_LINE_TOOLTIP,
            popper: {
              anchorEl: scrubbed === null ? null : tickRefs.current[scrubbed],
            },
          }}
          title={
            scrubbed === null ? (
              ""
            ) : (
              <BattleTooltipText battle={timeline[scrubbed]} />
            )
          }
        >
          <span className="recent-form-scrub-anchor" aria-hidden="true" />
        </AppTooltip>
      )}
    </>
  );
}

/**
 * Height of the sticky player navigation (layout.css), which changes with
 * the screen width. 0 where the page has none.
 */
function useNavHeight(): number {
  const [height, setHeight] = useState(0);
  useEffect(() => {
    const nav = document.querySelector(".player-nav-container");
    if (!nav) return;
    const observer = new ResizeObserver(() =>
      setHeight(nav.getBoundingClientRect().height),
    );
    observer.observe(nav);
    return () => observer.disconnect();
  }, []);
  return height;
}

// NOTE The strip's look at both ends of the morph. The full values match
// .recent-form-strip and its ticks, the compact ones .recent-form-pinned, both
// in recentForm.css. Width and gap stay, so no bar moves sideways. The
// compact height comes from --pinned-strip-height, which grows on phones.
const FULL_STRIP = { radius: 4, draw: 8 };
const COMPACT_STRIP = { radius: 2, draw: 4 };
// Space above the pinned strip, .recent-form-pinned's padding
const PINNED_PADDING = 4;
// Scrolling this far after the card's strip reaches the navigation turns it
// into the compact copy, one strip height, so the bars shrink as fast as the
// card leaves
const MORPH_DISTANCE = 64; // px
// The battle being read is the one crossing this line, as a share of the
// screen below the pinned strip
const READING_LINE = 0.3;

const lerp = (from: number, to: number, progress: number) =>
  from + (to - from) * progress;

// Starts and ends at rest, so the bars set off from the card's strip and
// settle into the compact one without a jolt in speed
const easeInOut = (progress: number) =>
  progress * progress * (3 - 2 * progress);

// Rounded to an even number of device pixels, so the middle line and the
// bar edges stay on whole pixels and do not shimmer while they shrink
const snapHeight = (px: number) => {
  const ratio = window.devicePixelRatio || 1;
  return (Math.round((px * ratio) / 2) * 2) / ratio;
};

/**
 * Win/loss record and a form strip of a run of battles.
 *
 * Once the card's strip reaches the bottom of the player navigation, a copy
 * takes its exact place and morphs, with the scroll, into a slim strip
 * pinned below the navigation, marking the battle being read. The copy is
 * sticky within the parent element, so the parent has to wrap exactly the
 * battles the strip shows: past the last of them, the copy scrolls away.
 *
 * @param battles - Newest first, as the battles API returns them
 * @param beforeDate - The applied "before" filter (UTC), if any
 * @param onSelect - Called with a clicked battle's index in `battles`
 * @param battleElementId - The element id of the battle at an index in
 *   `battles`, to mark the one being read in the pinned strip
 * @param ongoing - Whether `battles` starts with the player's newest stored
 *   battle. Only then can a streak into it still be going, so the streak
 *   sticker shows only then.
 */
export function RecentForm({
  battles,
  beforeDate,
  onSelect,
  battleElementId,
  ongoing = false,
}: Readonly<{
  battles: Battle[];
  beforeDate?: string;
  onSelect?: (index: number) => void;
  battleElementId?: (index: number) => string;
  ongoing?: boolean;
}>) {
  const navHeight = useNavHeight();
  const sectionRef = useRef<HTMLElement>(null);
  const stripRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef<HTMLDivElement>(null);
  // Index in `battles` of the battle being read while pinned
  const [current, setCurrent] = useState<number | null>(null);

  // Follows the scroll every frame. The morph and the swap between the two
  // strips are written to the DOM directly: through React state the swap
  // landed a frame after the morph, showing the card's strip already under
  // the navigation or the copy still shrunk, and a render per frame would
  // redraw both strips and their tooltips. Checked on scroll, since an
  // IntersectionObserver stays silent when a jump (restored position, scroll
  // to top) carries the strip from below the screen straight to above it.
  useEffect(() => {
    const section = sectionRef.current;
    const strip = stripRef.current;
    const pin = pinnedRef.current;
    if (!section || !strip || !pin) return;
    const reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;

    const update = () => {
      const from = strip.getBoundingClientRect();
      const box = pin.getBoundingClientRect();
      const compactHeight = parseFloat(
        getComputedStyle(pin).getPropertyValue("--pinned-strip-height"),
      );
      // Where the copy's strip sits: right on the card's strip at the start
      const start = navHeight + PINNED_PADDING;
      let progress = Math.min(
        Math.max((start - from.top) / MORPH_DISTANCE, 0),
        1,
      );
      if (reducedMotion && progress > 0) progress = 1;
      const eased = easeInOut(progress);
      const isPinned = from.top < start;

      // Read before the writes below, which would force another layout
      let reading: number | null = null;
      if (isPinned && battleElementId) {
        const top = box.bottom;
        const line = top + (window.innerHeight - top) * READING_LINE;
        // The last battle starting above the line, so the gap between two
        // cards still counts as the upper one
        for (let i = 0; i < battles.length; i++) {
          const card = document.getElementById(battleElementId(i));
          if (!card || card.getBoundingClientRect().top > line) break;
          reading = i;
        }
      }

      pin.classList.toggle("is-pinned", isPinned);
      section.classList.toggle("is-strip-pinned", isPinned);

      // Lined up with the card's strip throughout, only the height shrinks
      const vars = {
        "--morph": eased,
        "--morph-left": `${from.left - box.left}px`,
        "--morph-width": `${from.width}px`,
        "--morph-height": `${snapHeight(lerp(from.height, compactHeight, eased))}px`,
        "--morph-radius": `${lerp(FULL_STRIP.radius, COMPACT_STRIP.radius, eased)}px`,
        "--morph-draw": `${lerp(FULL_STRIP.draw, COMPACT_STRIP.draw, eased)}px`,
      };
      for (const [name, value] of Object.entries(vars)) {
        pin.style.setProperty(name, String(value));
      }

      // Changes only when crossing into another battle, and fades in with
      // the morph, so a render landing a frame later does not show
      setCurrent(reading);
    };

    // Browsers fire scroll at most once per frame, right before rendering,
    // so updating in the handler lands in the frame being drawn. Deferring
    // to requestAnimationFrame could miss it.
    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
    };
  }, [navHeight, battles.length, battleElementId]);

  if (battles.length === 0) return null;

  const count = (result: Result) =>
    battles.filter((b) => b.gameResult === result).length;
  const wins = count("Victory");
  const losses = count("Defeat");
  const draws = count("Draw");
  const winRate = Math.round((wins / battles.length) * 100);

  // The run the newest battle belongs to
  const latest = battles[0].gameResult;
  let streak = 1;
  while (streak < battles.length && battles[streak].gameResult === latest) {
    streak++;
  }
  const showStreak = ongoing && streak >= STREAK_MIN && latest !== "Draw";
  // Only these battles are known, so a run through all of them may be longer
  const streakFigure = streak === battles.length ? `${streak}+` : `${streak}`;

  // Read left to right like a time axis, so the newest battle ends the row.
  const timeline = [...battles].reverse();
  const selectFromTimeline =
    onSelect && ((i: number) => onSelect(timeline.length - 1 - i));

  return (
    <>
      {/* Ahead of the card, so it is already stuck below the navigation when
          the card's strip arrives there. Takes no space in the list
          (recentForm.css) and, hidden, leaves the tab order. */}
      <div
        ref={pinnedRef}
        className="recent-form-pinned"
        style={{ top: navHeight }}
      >
        <FormStrip
          timeline={timeline}
          onSelect={selectFromTimeline}
          label="Pinned results"
          scrub
          current={current === null ? null : timeline.length - 1 - current}
        />
      </div>

      <section ref={sectionRef} className="recent-form" aria-label="Form">
        <div className="recent-form-summary">
          <span className="recent-form-title">
            Form over last {battles.length} battles
            {beforeDate && ` before ${datetimeToLocale(beforeDate)}`}
          </span>
          <div className="recent-form-figures">
            <div className="recent-form-figure">
              <span
                className="recent-form-record"
                aria-label={`${wins} wins, ${losses} losses${
                  draws > 0 ? `, ${draws} draws` : ""
                }`}
              >
                <span className="recent-form-win">{wins}</span>
                <span className="recent-form-separator">-</span>
                <span className="recent-form-loss">{losses}</span>
                {draws > 0 && (
                  <>
                    <span className="recent-form-separator">-</span>
                    <span className="recent-form-draw">{draws}</span>
                  </>
                )}
              </span>
              <span className="recent-form-label">
                {draws > 0 ? "W-L-D" : "W-L"}
              </span>
            </div>
            <div className="recent-form-figure">
              <span className="recent-form-rate">{winRate}%</span>
              <span className="recent-form-label">win rate</span>
            </div>
          </div>
          {showStreak && (
            <Sticker
              tone={latest === "Victory" ? "orange" : "red"}
              className="recent-form-streak"
            >
              <span className="sticker-figure">{streakFigure}</span>
              <span className="sticker-caption">
                {latest === "Victory" ? "win" : "loss"}
                <br />
                streak
              </span>
            </Sticker>
          )}
        </div>

        {/* Wins rise above the line and losses drop below it, so the shape
            alone tells them apart. */}
        <div ref={stripRef} className="recent-form-strip-slot">
          <FormStrip
            timeline={timeline}
            onSelect={selectFromTimeline}
            label="Results"
          />
        </div>
      </section>
    </>
  );
}
