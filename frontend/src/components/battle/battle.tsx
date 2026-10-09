import { memo } from "react";
import { Link } from "react-router-dom";
import { DeckComponent } from "../deck/deck";
import { CopyableTag } from "../copyableTag/copyableTag";
import { BattlePlayerInfo } from "../battlePlayerInfo/battlePlayerInfo";
import type { Battle, Player } from "../../types/lastBattles";
import type { CardMeta } from "../../types/cards";
import { Crown } from "lucide-react";
import { datetimeToLocale } from "../../utils/datetime";
import { mapInternalNameToDisplayName } from "../../utils/gameModes";
import { normalizePlayerTag } from "../../utils/playerTag";
import "./battle.css";

export const BattleComponent = memo(function BattleComponent({
  battle,
  cards,
  playerTag,
  onOwnDeckOpen,
}: Readonly<{
  battle: Battle;
  cards: CardMeta[];
  // Tag of the player whose battles are shown. Only that player's deck is a
  // link, a 2v2 teammate's decks are not on that player's decks page.
  playerTag?: string;
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
          <Crown className="battle-component-crown battle-component-team-crown battle-component-crown-blue" />
          <span className="battle-component-score-text">
            {battle.team[0].crowns} - {battle.opponent[0].crowns}
          </span>
          <Crown className="battle-component-crown battle-component-opponent-crown battle-component-crown-red" />
        </div>
      </div>
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
                    title="Show this deck's statistics"
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
