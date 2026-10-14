/**
 * Tower health of a battle and whether it was a close call.
 *
 * Battles report the hit points a tower has left, never its full amount, and
 * no King Level either. The full amount comes from tables per level:
 *
 * - Crown towers (the two side towers) take the Tower Troop's hit points at
 *   the troop's level, both of which every battle records.
 * - The King Tower takes the King Level. The page's own player gets it from
 *   battles where their King Tower was never hit (see kingLevelTimeline),
 *   kept apart per mode cap.
 *   Any other side uses its Tower Troop level: the King Level caps the
 *   troop, and in 99% of stored battles with an untouched King Tower both
 *   match. A King Tower with more hit points than that level allows proves a
 *   higher King Level, which is then used instead.
 *
 * Each side gets its own levels, so a level 15 team against a level 11
 * opponent shows both correctly.
 */
import type { Battle, Player } from "../types/lastBattles";

// A standing tower at or below this share of a full one counts as nearly
// destroyed: very little health left, though how many hits that is depends
// on attack damage the battle does not record. 10% marks 9.3% of stored battles as close
// (October 2026). An extra absolute floor (one small spell, ~200 HP) changed
// none of them, since 10% of a level 11+ tower is already above it.
// Additionally both card/troop damage and tower HP scale with level, so a close
// win/loss is the same percentage wise across all arenas and levels.
const CLOSE_TOWER_RATIO = 0.1;

// NOTE Full hit points per level, on the 1 to 16 scale battles report.
// Levels seen in stored battles use the most common value there, which
// matches the published stats (Liquipedia, Fandom). Levels never seen follow
// each troop's fixed share of the Tower Princess, rounded; the comments name
// them. Exact matches in stored 1v1 battles (October 2026), per level:
//
//   King Tower      1-3: 2-5    4-10: 10-115    11-16: 862-564,812
//   Tower Princess  1-4: 1-9    5-10: 11-57     11-16: 454-205,795
//   Cannoneer       8-10: 2-3   11, 13-16: 10-2,073      6, 7, 12: none
//   Dagger Duchess  9-10: 3-4   11-16: 16-9,374
//   Royal Chef      9: 1        10-16: 10-6,131
//
// IMPORTANT: Update all tables on a balance change or a
// new level cap; older battles keep the values of their time, which the
// observed-value fallback in sideTowers absorbs, the king tower does NOT.
const KING_HP = [
  2400, 2568, 2736, 2904, 3096, 3312, 3528, 3768, 4008, 4392, 4824, 5304, 5832,
  6408, 7032, 7728,
];
const TOWER_PRINCESS_ID = 159000000;
// Each troop starts at its rarity's first level: common 1, epic 6,
// legendary 9. The catalogue lists no other Tower Troops.
const TOWER_TROOPS: Record<
  number,
  { name: string; minLevel: number; hp: number[] }
> = {
  [TOWER_PRINCESS_ID]: {
    name: "Princess tower",
    minLevel: 1,
    hp: [
      1400, 1512, 1624, 1750, 1890, 2030, 2184, 2352, 2534, 2786, 3052, 3346,
      3668, 4032, 4424, 4858,
    ],
  },
  // Epic. 6/7 of the Tower Princess; levels 6, 7 and 12 derived from it.
  159000001: {
    name: "Cannoneer tower",
    minLevel: 6,
    hp: [1740, 1872, 2016, 2172, 2388, 2616, 2868, 3144, 3456, 3792, 4164],
  },
  // Legendary. 2768/3052 of the Tower Princess.
  159000002: {
    name: "Dagger Duchess tower",
    minLevel: 9,
    hp: [2298, 2527, 2768, 3035, 3327, 3657, 4013, 4406],
  },
  // Legendary. 2703/3052 of the Tower Princess.
  159000004: {
    name: "Royal Chef tower",
    minLevel: 9,
    hp: [2244, 2467, 2703, 2963, 3248, 3571, 3918, 4302],
  },
};
const MAX_LEVEL = KING_HP.length;

// The 2v2 King Tower has 20% more hit points than a 1v1 one of its level
const TWO_VS_TWO_KING_FACTOR = 1.2;

// Towers up to this far above the table come from before a balance change
// (Royal Chef had 8% more until October 2025) and count as full. Further
// above, the battle had boosted towers.
const HISTORY_TOLERANCE = 1.15;
// Chaos and Crazy Arena events double every tower. The only boost seen in
// stored battles; others leave the health share unknown.
const EVENT_BOOST = 2;

export type Tower = {
  kind: "king" | "crown";
  // e.g. "King tower", "Cannoneer tower"
  name: string;
  // Remaining hit points, 0 once destroyed, null when the battle does not
  // record them
  hp: number | null;
  // Share of a full tower, 0 to 1, null when the full amount is unknown
  ratio: number | null;
};

export type SideTowers = {
  king: Tower;
  crowns: [Tower, Tower];
  // No tower of the side took any damage
  untouched: boolean;
};

export type BattleTowers = {
  team: SideTowers;
  opponent: SideTowers;
};

// A crown tower's troop: its name and full hit points
type CrownTroop = { name: string; full: number };

type FullHitPoints = {
  // Null when the King Tower is beyond every level and boost
  king: number | null;
  // How far the battle boosted its towers (see battleBoost)
  boost: number;
  // 1.2 for a 2v2 King Tower, else 1
  kingFactor: number;
  // One troop for both crown towers, or in 2v2 one per player when the two
  // players' troops differ
  crowns: CrownTroop[];
};

function isLevel(level: number | undefined): level is number {
  return level !== undefined && level >= 1 && level <= MAX_LEVEL;
}

// The mode, troop and troop level a battle ran the player's towers at.
// Modes like friendlies, drafts and events cap both the troop and the King
// Tower (at 11), so a King Level seen in one context says nothing about
// another. The mode is part of it, since an uncapped player can use a troop
// at 11 too, under a higher King.
function towerContext(battle: Battle): string | undefined {
  const troop = battle.team[0]?.supportCards?.[0];
  return troop && isLevel(troop.level)
    ? `${battle.gameMode}:${troop.id}:${troop.level}`
    : undefined;
}

/**
 * The page player's King Level at each of their battles. A 1v1 battle that
 * ended with exactly a level's full King Tower hit points shows that level,
 * since the tower was never hit.
 *
 * Levels only carry over between battles of the same mode with the same
 * Tower Troop at the same level (see towerContext), so a level 16 King from
 * Ladder never lands on a friendly capped at 11. Within one context the level never goes down:
 * each one found holds for every later battle until a higher one shows up,
 * and battles before the first one found take that first level.
 *
 * NOTE The profile's kingTowerLevel is the current level only, with no
 * history, and capped modes ignore it, so it cannot replace this.
 *
 * @param battles - The player's battles, newest first, seen from their side
 * @returns The King Level per battle in the same order, undefined where no
 *   battle of its context had an untouched King Tower
 */
export function kingLevelTimeline(battles: Battle[]): (number | undefined)[] {
  const contexts = battles.map((battle) =>
    battle.team.length === 1 ? towerContext(battle) : undefined,
  );
  const seen = battles.map((battle, i) => {
    if (!contexts[i]) return undefined;
    const index = KING_HP.indexOf(battle.team[0].kingTowerHitPoints);
    return index === -1 ? undefined : index + 1;
  });

  // The oldest level seen per context also covers that context's earlier
  // battles
  const current = new Map<string, number>();
  for (let i = battles.length - 1; i >= 0; i--) {
    const context = contexts[i];
    const level = seen[i];
    if (context && level && !current.has(context)) current.set(context, level);
  }

  // Oldest first, carrying each context's highest level so far forward
  const levels: (number | undefined)[] = new Array(battles.length);
  for (let i = battles.length - 1; i >= 0; i--) {
    const context = contexts[i];
    if (!context) continue;
    const level = Math.max(current.get(context) ?? 0, seen[i] ?? 0);
    if (level) current.set(context, level);
    levels[i] = level || undefined;
  }
  return levels;
}

/**
 * A player's crown tower troop at its level, or null without Tower Troop
 * data (older battles, some events) or at a level the troop cannot have.
 */
function crownTroop(player: Player): CrownTroop | null {
  const troop = player.supportCards?.[0];
  if (!troop || !isLevel(troop.level)) return null;
  // A troop missing from the tables counts as a Tower Princess for its hit
  // points, under its own name
  const known = TOWER_TROOPS[troop.id];
  const table = known ?? TOWER_TROOPS[TOWER_PRINCESS_ID];
  const full = table.hp[troop.level - table.minLevel];
  if (full === undefined) return null;
  return { name: known?.name ?? `${troop.name} tower`, full };
}

/**
 * The crown tower troops of a side, one per distinct troop, or null when a
 * player of the side has no usable Tower Troop data.
 */
function crownTroops(players: Player[]): CrownTroop[] | null {
  const troops = players.map(crownTroop);
  if (troops.some((troop) => troop === null)) return null;
  return (troops as CrownTroop[]).filter(
    (troop, i, all) =>
      all.findIndex((t) => t.name === troop.name && t.full === troop.full) ===
      i,
  );
}

/**
 * Full hit points of one side's towers from its Tower Troops.
 *
 * @param players - The side's players, two in 2v2
 * @param crowns - The side's crown troops (see crownTroops)
 * @param kingLevelHint - The King Level known from other battles, if any
 * @param boost - The battle's tower boost (see battleBoost)
 */
function tableHitPoints(
  players: Player[],
  crowns: CrownTroop[],
  kingLevelHint: number | undefined,
  boost: number,
): FullHitPoints {
  const kingFactor = players.length > 1 ? TWO_VS_TWO_KING_FACTOR : 1;
  // Rounded like the game: 1.2 x 4824 shows as 5789, not 5788.8
  const kingHp = (level: number) =>
    Math.round(KING_HP[level - 1] * kingFactor) * boost;
  const observed = players[0].kingTowerHitPoints;
  const troopLevel = Math.max(
    ...players.map((p) => p.supportCards?.[0]?.level ?? 0),
  );
  const boostedCrowns = crowns.map((troop) => ({
    ...troop,
    full: troop.full * boost,
  }));
  const full = (king: number | null) => ({
    king,
    boost,
    kingFactor,
    crowns: boostedCrowns,
  });

  // An untouched King Tower in this very battle shows its level exactly,
  // which beats anything learned from other battles
  const untouchedLevel = KING_HP.findIndex(
    (_, i) => i + 1 >= troopLevel && kingHp(i + 1) === observed,
  );
  if (untouchedLevel !== -1) return full(kingHp(untouchedLevel + 1));

  // The King Level is at least the troop level it caps
  let kingLevel = Math.max(kingLevelHint ?? 0, troopLevel);
  // A boost scales the level the troop shows, nothing else is known
  if (boost !== 1) return full(Math.max(kingHp(kingLevel), observed));
  // A King Tower with more hit points than the level allows proves a higher
  // one. Beyond every level it is an unknown boost of the King Tower alone.
  while (kingLevel < MAX_LEVEL && kingHp(kingLevel) < observed) kingLevel++;
  if (observed > kingHp(kingLevel) * HISTORY_TOLERANCE) return full(null);
  return full(Math.max(kingHp(kingLevel), observed));
}

/**
 * Without Tower Troop data, the healthiest towers of the battle set a lower
 * bound for the level, rounded up to the next one in the tables. Towers
 * beyond every level leave the full amount unknown (null).
 */
function estimatedHitPoints(players: Player[]): FullHitPoints | null {
  const princessHp = TOWER_TROOPS[TOWER_PRINCESS_ID].hp;
  const king = Math.max(...players.map((p) => p.kingTowerHitPoints));
  const crown = Math.max(
    0,
    ...players.flatMap((p) => p.princessTowersHitPoints ?? []),
  );
  const level = KING_HP.findIndex(
    (hp, i) => hp >= king && princessHp[i] >= crown,
  );
  if (level === -1) return null;
  return {
    king: KING_HP[level],
    boost: 1,
    kingFactor: 1,
    crowns: [{ name: "Crown tower", full: princessHp[level] }],
  };
}

function tower(
  kind: Tower["kind"],
  name: string,
  hp: number | null,
  full: number | null,
): Tower {
  const ratio =
    hp === null || full === null || full <= 0 ? null : Math.min(1, hp / full);
  return { kind, name, hp, ratio };
}

/**
 * How far above the tables the battle's towers were, for both sides: 1 for
 * regular battles (balance changes stay within HISTORY_TOLERANCE),
 * EVENT_BOOST when every crown tower fits it, null when it cannot be told
 * and the health shares stay unknown.
 *
 * Only crown towers decide it: their full amount follows exactly from the
 * troop and its level, while a King Tower above the troop's level can just
 * be a higher King Level.
 */
function battleBoost(
  sides: { player: Player; crowns: CrownTroop[] }[],
): number | null {
  const fits = (factor: number) =>
    sides.every(({ player, crowns }) => {
      const crownFull = Math.max(...crowns.map((troop) => troop.full));
      return (player.princessTowersHitPoints ?? []).every(
        (hp) => hp <= crownFull * factor,
      );
    });
  if (fits(HISTORY_TOLERANCE)) return 1;
  if (fits(EVENT_BOOST)) return EVENT_BOOST;
  return null;
}

/**
 * Which troop each crown tower slot belongs to. With one troop it is both.
 * With two (2v2 players with different troops) the API does not say which
 * tower is whose: a tower with more hit points than one troop can have is
 * the other's, and the second tower is then the remaining troop's. A tower
 * that fits both stays unassigned and is measured against the lower full
 * hit points, which can only make the battle look less close.
 */
function assignCrownTroops(
  standing: number[],
  crowns: CrownTroop[],
): CrownTroop[] {
  if (crowns.length === 1) return [crowns[0], crowns[0]];
  const unknown: CrownTroop = {
    name: "Crown tower",
    full: Math.min(...crowns.map((c) => c.full)),
  };
  const slots = [0, 1].map((i) => {
    const hp = standing[i];
    if (hp === undefined) return undefined;
    const fits = crowns.filter((c) => hp <= c.full);
    return fits.length === 1 ? fits[0] : undefined;
  });
  const assigned = slots.filter((slot) => slot !== undefined);
  return slots.map((slot) => {
    if (slot) return slot;
    // The other tower is known, so this one is the other player's
    if (assigned.length === 1) {
      return crowns.find((c) => c !== assigned[0]) ?? unknown;
    }
    return unknown;
  });
}

/**
 * One side's towers.
 *
 * @param player - The side's first player, whose hit points are the side's
 * @param full - Full hit points, null when they are unknown
 * @param enemyCrowns - Crowns the other side took, to check the crown towers
 */
function sideTowers(
  player: Player,
  full: FullHitPoints | null,
  enemyCrowns: number,
): SideTowers {
  const kingHp = player.kingTowerHitPoints;
  // The API omits the list when no crown tower stands. With the King Tower
  // standing, the enemy's crowns say how many fell, so a missing list that
  // contradicts them (duels, boat battles) is unrecorded, not destroyed.
  const expected = kingHp > 0 ? 2 - Math.min(2, enemyCrowns) : 0;
  const recorded = player.princessTowersHitPoints != null || expected === 0;
  const standing = player.princessTowersHitPoints ?? [];
  const troops = full ? assignCrownTroops(standing, full.crowns) : [null, null];
  // Within HISTORY_TOLERANCE (checked by battleBoost) a tower above the table
  // is a full one from before a balance change
  const crowns = [0, 1].map((i) => {
    const hp = recorded ? (standing[i] ?? 0) : null;
    const troop = troops[i];
    const fullHp = troop && hp !== null ? Math.max(troop.full, hp) : null;
    return tower("crown", troop?.name ?? "Crown tower", hp, fullHp);
  }) as [Tower, Tower];
  // Untouched only on exact full values from the tables, which boosted and
  // unknown towers never match. Any King Level counts, so an uncertain level
  // cannot hide it.
  const kingUntouched =
    full !== null &&
    full.boost === 1 &&
    KING_HP.some((hp) => Math.round(hp * full.kingFactor) === kingHp);
  const crownsUntouched =
    full !== null &&
    full.boost === 1 &&
    standing.length === 2 &&
    standing.every((hp) => full.crowns.some((troop) => troop.full === hp));
  return {
    king: tower(
      "king",
      "King tower",
      kingHp,
      full?.king != null ? Math.max(full.king, kingHp) : null,
    ),
    crowns,
    untouched: kingUntouched && crownsUntouched,
  };
}

/**
 * The remaining health of both sides' towers.
 *
 * NOTE In 2v2 both players of a side report the same, shared towers, so the
 * first player's hit points are the side's. Each player's Tower Troop holds
 * one of the two crown towers (see assignCrownTroops).
 *
 * @param battle - The battle, seen from the team's side
 * @param teamKingLevel - The team player's King Level at this battle (see
 *   kingLevelTimeline), used in 1v1 only: a 2v2 King Tower is shared
 * @returns Both sides' towers, or null if the battle has no tower data
 */
export function getBattleTowers(
  battle: Battle,
  teamKingLevel?: number,
): BattleTowers | null {
  const team = battle.team[0];
  const opponent = battle.opponent[0];
  if (
    typeof team?.kingTowerHitPoints !== "number" ||
    typeof opponent?.kingTowerHitPoints !== "number"
  ) {
    return null;
  }
  const teamHint = battle.team.length === 1 ? teamKingLevel : undefined;
  const teamCrowns = crownTroops(battle.team);
  const opponentCrowns = crownTroops(battle.opponent);

  // Without Tower Troop data on either side, only the estimate is left,
  // which a boosted battle leaves unknown
  if (!teamCrowns || !opponentCrowns) {
    const estimate = estimatedHitPoints([team, opponent]);
    return {
      team: sideTowers(team, estimate, opponent.crowns),
      opponent: sideTowers(opponent, estimate, team.crowns),
    };
  }
  const boost = battleBoost([
    { player: team, crowns: teamCrowns },
    { player: opponent, crowns: opponentCrowns },
  ]);
  if (boost === null) {
    return {
      team: sideTowers(team, null, opponent.crowns),
      opponent: sideTowers(opponent, null, team.crowns),
    };
  }
  return {
    team: sideTowers(
      team,
      tableHitPoints(battle.team, teamCrowns, teamHint, boost),
      opponent.crowns,
    ),
    opponent: sideTowers(
      opponent,
      tableHitPoints(battle.opponent, opponentCrowns, undefined, boost),
      team.crowns,
    ),
  };
}

export type CloseCall = {
  // The winner's nearly destroyed towers that decided it, weakest first
  towers: Tower[];
  // What losing them would have done: the King Tower falling loses the
  // game, the crown towers falling tie it
  outcome: "lost" | "tied";
};

function isNearlyDown(tower: Tower): boolean {
  return (
    tower.hp !== null &&
    tower.hp > 0 &&
    tower.ratio !== null &&
    tower.ratio <= CLOSE_TOWER_RATIO
  );
}

/**
 * Whether the loser came very close to changing the result: the winner's
 * King Tower nearly fell (which ends the game the other way), or every crown
 * tower the loser still needed to tie was nearly down. A lead of m crowns
 * needs m more towers to tie, so with one crown that is the weakest tower,
 * with two both of them.
 *
 * Overtime tiebreakers are often the closest games of all, but that comes
 * down to the same HP difference call like regular battles.
 *
 * @param battle - The battle, seen from the team's side
 * @param towers - The battle's towers (see getBattleTowers)
 * @returns The towers and what they would have changed, or null if the
 *   battle was not close
 */
export function getCloseCall(
  battle: Battle,
  towers: BattleTowers,
): CloseCall | null {
  if (battle.gameResult === "Draw") return null;
  const won = battle.gameResult === "Victory";
  const winner = won ? towers.team : towers.opponent;
  const lead = won
    ? battle.team[0].crowns - battle.opponent[0].crowns
    : battle.opponent[0].crowns - battle.team[0].crowns;

  if (isNearlyDown(winner.king)) {
    return { towers: [winner.king], outcome: "lost" };
  }
  const needed = winner.crowns
    .filter((t) => t.hp !== null && t.hp > 0)
    .sort((a, b) => (a.ratio ?? 1) - (b.ratio ?? 1))
    .slice(0, lead);
  if (lead >= 1 && needed.length === lead && needed.every(isNearlyDown)) {
    return { towers: needed, outcome: "tied" };
  }
  return null;
}
