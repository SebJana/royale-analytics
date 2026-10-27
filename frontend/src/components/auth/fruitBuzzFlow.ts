import type {
  FruitBuzzRoundResponse,
  FruitBuzzStatusResponse,
} from "../../types/auth";

export type VisibleCard = { roundIndex: number; imageId: string; url: string };

/**
 * Rebuild a lost action response only when status confirms that the requested
 * round was the last one committed. Older results must never score twice.
 */
export function recoverSettledRound(
  status: FruitBuzzStatusResponse,
  requestedRound: number,
): FruitBuzzRoundResponse | null {
  if (
    status.current_round <= requestedRound ||
    status.last_round_index !== requestedRound ||
    !status.last_round_result ||
    !status.last_round_reason ||
    status.last_round_clear_cards === null
  )
    return null;
  return {
    ...status,
    round_result: status.last_round_result,
    round_reason: status.last_round_reason,
    late_by_ms: status.last_round_late_by_ms,
    winning_card_ids: status.last_round_winning_card_ids,
    clear_cards: status.last_round_clear_cards,
    next_card:
      status.prepared_cards.find(
        (card) => card.round_index === status.current_round,
      ) ?? null,
    preloaded_card: status.prepared_cards.at(-1) ?? null,
  };
}

export function afterRound(
  cards: VisibleCard[],
  clearCards: boolean,
): VisibleCard[] {
  return clearCards ? [] : cards;
}

export function addVisibleCard(
  cards: VisibleCard[],
  card: VisibleCard,
  limit: number,
): VisibleCard[] {
  return [...cards, card].slice(-limit);
}

/**
 * Give one buzz or timed advance ownership of a round. An early next-card 409
 * releases that ownership so the same round can try again after its deadline.
 */
export function createSettlementGate() {
  let current = -1;
  let claimed = false;
  return {
    open(round: number) {
      current = round;
      claimed = false;
    },
    claim(round: number) {
      if (round !== current || claimed) return false;
      claimed = true;
      return true;
    },
    release(round: number) {
      if (round === current) claimed = false;
    },
  };
}

/** Schedule against a saved timestamp so work before this call cannot add time. */
export function scheduleNextCard(due: number, onNext: () => void): () => void {
  const timer = setTimeout(onNext, Math.max(0, due - performance.now()));
  return () => clearTimeout(timer);
}
