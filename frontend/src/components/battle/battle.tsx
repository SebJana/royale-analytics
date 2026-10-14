import { memo } from "react";
import { Link } from "react-router-dom";
import { DeckComponent } from "../deck/deck";
import { CopyableTag } from "../copyableTag/copyableTag";
import { Sticker } from "../sticker/sticker";
import { TowerHealth } from "../towerHealth/towerHealth";
import { AppTooltip } from "../tooltip/tooltip";
import { BattlePlayerInfo } from "../battlePlayerInfo/battlePlayerInfo";
import type { Battle, Player } from "../../types/lastBattles";
import type { CardMeta } from "../../types/cards";
import { Crown } from "lucide-react";
import { datetimeToLocale } from "../../utils/datetime";
import { mapInternalNameToDisplayName } from "../../utils/gameModes";
import { normalizePlayerTag } from "../../utils/playerTag";
import { formatNumber } from "../../utils/number";
import { getBattleTowers, getCloseCall } from "../../utils/towers";
import type { CloseCall } from "../../utils/towers";
import "./battle.css";

/**
 * Why a battle was close, from the page player's side, e.g. "Your Royal Chef
 * tower was left at 67 HP. Losing it would have tied the game." It states
 * what remained and what losing it meant, not how many hits that was: the
 * attack damage is not known.
 */
function closeCallText(call: CloseCall, won: boolean): string {
  const owner = won ? "Your" : "Their";
  const hp = call.towers.map((t) => `${formatNumber(t.hp ?? 0)} HP`);
  const single = call.towers.length === 1;
  const towers = single
    ? `${owner} ${call.towers[0].name} was left at ${hp[0]}.`
    : `${owner} last ${call.towers.length} crown towers were left at ${hp.join(" and ")}.`;
  const loss = single ? "Losing it" : "Losing them";
  const result =
    call.outcome === "tied"
      ? "tied the game"
      : won
        ? "lost the game"
        : "won you the game";
  return `${towers} ${loss} would have ${result}.`;
}

export const BattleComponent = memo(function BattleComponent({
  battle,
  cards,
  playerTag,
  onOwnDeckOpen,
  kingLevel,
}: Readonly<{
  battle: Battle;
  cards: CardMeta[];
  // Tag of the player whose battles are shown. Only that player's deck is a
  // link, a 2v2 teammate's decks are not on that player's decks page.
  playerTag?: string;
  // The page player's King Level at this battle (see kingLevelTimeline),
  // for the tower health
  kingLevel?: number;
  // Runs before the decks page opens, e.g. to store the deck's filters
  onOwnDeckOpen?: (battle: Battle, player: Player) => void;
}>) {
  const ownTag = playerTag ? normalizePlayerTag(playerTag) : null;
  const isOwnPlayer = (player: Player) =>
    ownTag !== null &&
    !!player.tag &&
    normalizePlayerTag(player.tag) === ownTag;

  // Map the color of the result
  const getResultColor = (result: string) => {
    switch (result.toLowerCase()) {
      case "victory":
        return "blue";
      case "defeat":
        return "red";
      case "draw":
        return "gray";
      default:
        return "gray";
    }
  };

  const gameMode = mapInternalNameToDisplayName(battle.gameMode);
  const towers = getBattleTowers(battle, kingLevel);
  const closeCall = towers ? getCloseCall(battle, towers) : null;
  const won = battle.gameResult === "Victory";

  // Three crowns end the battle at once: gold when the player did it, red
  // when it happened to them
  const badges = [
    won && battle.team[0].crowns === 3 && (
      <Sticker key="3crown" tone="gold" className="battle-component-sticker">
        3 crown
      </Sticker>
    ),
    battle.gameResult === "Defeat" && battle.opponent[0].crowns === 3 && (
      <Sticker key="3crowned" tone="red" className="battle-component-sticker">
        3 crowned
      </Sticker>
    ),
    // Won without a single tower taking damage
    won && towers?.team.untouched && (
      <AppTooltip
        key="flawless"
        title="None of your towers took any damage"
        tapToShow
      >
        <Sticker tone="gold" className="battle-component-sticker">
          flawless
        </Sticker>
      </AppTooltip>
    ),
    closeCall && (
      <AppTooltip key="close" title={closeCallText(closeCall, won)} tapToShow>
        <Sticker
          tone={won ? "orange" : "red"}
          className="battle-component-sticker"
        >
          {won ? "close win" : "close loss"}
        </Sticker>
      </AppTooltip>
    ),
  ].filter(Boolean);

  return (
    <div className="battle-component-container">
      <div className="battle-component-header">
        <div className="battle-component-header-left">
          <div
            className={`battle-component-header-result battle-component-result-${getResultColor(
              battle.gameResult,
            )}`}
          >
            {battle.gameResult}
          </div>
          <span className="battle-component-battle-time">
            {datetimeToLocale(battle.battleTime)}
          </span>
        </div>
        <h2 className="battle-component-game-mode">{gameMode}</h2>
        <div className="battle-component-score">
          {badges.length > 0 && (
            <span className="battle-component-badges">{badges}</span>
          )}
          <span className="battle-component-scoreline">
            <Crown className="battle-component-crown battle-component-team-crown battle-component-crown-blue" />
            <span className="battle-component-score-text">
              {battle.team[0].crowns} - {battle.opponent[0].crowns}
            </span>
            <Crown className="battle-component-crown battle-component-opponent-crown battle-component-crown-red" />
          </span>
        </div>
      </div>
      {towers && <TowerHealth towers={towers} />}
      <div className="battle-component-decks">
        <div className="battle-component-col battle-component-team">
          {battle.team?.map((t, i) => {
            const deck = (
              <DeckComponent
                deck={t.cards ?? []}
                support={t.supportCards ?? []}
                supportSide="left"
                cards={cards ?? []}
                elixirLeaked={t.elixirLeaked}
              />
            );
            return (
              <section
                key={`${battle.battleTime}-team-${t.tag ?? i}`}
                className="battle-component-player-block"
              >
                {isOwnPlayer(t) ? (
                  // The page's own player: a link would lead to this page.
                  <div className="battle-component-player-info battle-component-player-info-left">
                    <h3 className="battle-component-player-name">
                      {t.name ?? `Player ${i + 1}`}
                    </h3>
                    {t.tag && (
                      <CopyableTag
                        tag={t.tag}
                        className="battle-component-player-tag"
                      />
                    )}
                  </div>
                ) : (
                  // A 2v2 teammate, linked like the opponents.
                  <BattlePlayerInfo
                    name={t.name}
                    tag={t.tag}
                    fallbackName={`Player ${i + 1}`}
                    className="battle-component-player-info battle-component-player-info-left"
                    nameClassName="battle-component-player-name"
                    tagClassName="battle-component-player-tag"
                  />
                )}
                {playerTag && onOwnDeckOpen && isOwnPlayer(t) ? (
                  <Link
                    to={`/player/${encodeURIComponent(playerTag)}/decks`}
                    className="battle-component-deck-link"
                    // Its cards have tooltips of their own, a second one for
                    // the whole deck would open together with theirs
                    aria-label="Show this deck's statistics"
                    onClick={() => onOwnDeckOpen(battle, t)}
                    // A middle click opens a new tab without a click event,
                    // and that tab reads the filters as well
                    onAuxClick={(event) => {
                      if (event.button === 1) onOwnDeckOpen(battle, t);
                    }}
                  >
                    {deck}
                  </Link>
                ) : (
                  deck
                )}
              </section>
            );
          })}
        </div>

        <div className="battle-component-col battle-component-opponent">
          {battle.opponent?.map((o, i) => (
            <section
              key={`${battle.battleTime}-opp-${o.tag ?? i}`}
              className="battle-component-player-block"
            >
              <BattlePlayerInfo
                name={o.name}
                tag={o.tag}
                fallbackName={`Player ${i + 1}`}
                className="battle-component-player-info battle-component-player-info-right"
                nameClassName="battle-component-player-name"
                tagClassName="battle-component-player-tag"
              />
              <DeckComponent
                deck={o.cards ?? []}
                support={o.supportCards ?? []}
                cards={cards ?? []}
                elixirLeaked={o.elixirLeaked}
              />
            </section>
          ))}
        </div>
      </div>
    </div>
  );
});
