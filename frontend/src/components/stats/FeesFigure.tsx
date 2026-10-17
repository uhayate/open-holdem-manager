const usd = (v: number) => `${v >= 0 ? '' : '-'}$${Math.abs(v).toFixed(2)}`;

interface FeesFigureProps {
  rakeUSD: number;
  jackpotUSD: number;
  rakePerBB: number;
  jackpotPerBB: number;
}

/**
 * Total fees = rake + jackpot, laid out on two short lines so neither one
 * wraps inside a narrow stat cell:
 *
 *   $10.62 ($7.67+$2.95)                 <- money: the total, then its split
 *   Fees · Rake 4.2/100 · Jackpot 1.6/100   <- rates, no currency
 *
 * The Graph footer and the Sessions summary strip both report this figure,
 * so it lives here rather than being copy-pasted into each page.
 *
 * Two layout details matter:
 * - The (x/100) brackets were dropped along with the dollar amounts. They
 *   only ever existed to fence the rate off from the money beside it, and at
 *   41 characters they were the exact margin that made this line wrap.
 * - Each segment is nowrap and the row is flex-wrap, so if a narrow cell ever
 *   does force a break it lands between "Rake" and "Jackpot" instead of
 *   splitting "$2.95" off from its own label.
 *
 * "Jackpot" is the word the hand history itself prints; it is not an
 * abbreviation, so it is deliberately left unexpanded.
 */
export default function FeesFigure({ rakeUSD, jackpotUSD, rakePerBB, jackpotPerBB }: FeesFigureProps) {
  const hasJackpot = jackpotUSD > 0;

  return (
    <>
      <div className="text-sm font-bold font-mono text-text-muted">
        {usd(rakeUSD + jackpotUSD)}
        {hasJackpot && (
          <span className="ml-1 text-[11px] font-normal opacity-70">
            ({usd(rakeUSD)}+{usd(jackpotUSD)})
          </span>
        )}
      </div>
      <div className="text-xs text-text-muted flex flex-wrap items-baseline gap-x-1.5">
        <span className="whitespace-nowrap">
          Fees · Rake <span className="font-mono">{rakePerBB.toFixed(1)}/100</span>
        </span>
        {hasJackpot && (
          <span className="whitespace-nowrap">
            · Jackpot <span className="font-mono">{jackpotPerBB.toFixed(1)}/100</span>
          </span>
        )}
      </div>
    </>
  );
}
