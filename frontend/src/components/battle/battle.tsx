import { memo } from "react";
import { DeckComponent } from "../deck/deck";
import type { Battle } from "../../types/lastBattles";
import type { CardMeta } from "../../types/cards";
import { Crown } from "lucide-react";
import { datetimeToLocale } from "../../utils/datetime";
import { mapInternalNameToDisplayName } from "../../utils/gameModes";
import "./battle.css";

export const BattleComponent = memo(function BattleComponent({
  battle,
  cards,
}: Readonly<{
  battle: Battle;
  cards: CardMeta[];
}>) {
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
          {battle.team?.map((t, i) => (
            <section
              key={`${battle.battleTime}-team-${t.tag ?? i}`}
              className="battle-component-player-block"
            >
              <div className="battle-component-player-info battle-component-player-info-left">
                <h3 className="battle-component-player-name">
                  {t.name ?? `Player ${i + 1}`}
                </h3>
                {t.tag && (
                  <span className="battle-component-player-tag">{t.tag}</span>
                )}
              </div>
              <DeckComponent
                deck={t.cards ?? []}
                support={t.supportCards ?? []}
                supportSide="left"
                cards={cards ?? []}
                elixirLeaked={t.elixirLeaked}
              />
            </section>
          ))}
        </div>

        <div className="battle-component-col battle-component-opponent">
          {battle.opponent?.map((o, i) => (
            <section
              key={`${battle.battleTime}-opp-${o.tag ?? i}`}
              className="battle-component-player-block"
            >
              <div className="battle-component-player-info battle-component-player-info-right">
                <h3 className="battle-component-player-name">
                  {o.name ?? `Player ${i + 1}`}
                </h3>
                {o.tag && (
                  <span className="battle-component-player-tag">{o.tag}</span>
                )}
              </div>
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
