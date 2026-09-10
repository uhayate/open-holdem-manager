import type { HandDetail, HandPlayerDetail, HandAction } from '@/lib/api';

// ── Anonymize ────────────────────────────────────────────────────────

/**
 * Replaces every non-hero screen name with a stable "Player N" label so a hand
 * can be displayed or screenshotted without leaking opponents' identities.
 * Labels are assigned in seat order so the same hand always maps identically.
 */
export function anonymizeHand(hand: HandDetail): HandDetail {
  const nameMap = new Map<string, string>();
  let counter = 1;
  const sorted = [...hand.players].sort((a, b) => a.seat - b.seat);
  for (const p of sorted) {
    if (p.is_hero) {
      nameMap.set(p.username, p.username);
    } else {
      nameMap.set(p.username, `Player ${counter}`);
      counter++;
    }
  }

  const players: HandPlayerDetail[] = hand.players.map((p) => ({
    ...p,
    username: nameMap.get(p.username) ?? p.username,
  }));

  const actions: HandAction[] = hand.actions.map((a) => ({
    ...a,
    player: nameMap.get(a.player) ?? a.player,
  }));

  return { ...hand, players, actions };
}
