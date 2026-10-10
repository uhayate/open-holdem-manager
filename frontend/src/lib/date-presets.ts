export type DatePreset = 'today' | 'week' | 'month' | 'all';

/**
 * Format a Date as `YYYY-MM-DD` in **local** time.
 *
 * Never use `d.toISOString().slice(0, 10)` for a calendar date: that converts
 * to UTC first, so in UTC+8 a local midnight (and anything before 08:00) lands
 * on the previous day. Clicking "Today" then queried yesterday's hands.
 */
export function toLocalDateString(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

/** Today's date in local time, as `YYYY-MM-DD`. */
export function todayLocalDate(): string {
  return toLocalDateString(new Date());
}

export function getPresetDates(preset: DatePreset): { date_from?: string; date_to?: string } {
  if (preset === 'all') return {};
  const now = new Date();
  if (preset === 'today') {
    return { date_from: toLocalDateString(now) };
  }
  if (preset === 'week') {
    const day = now.getDay();
    const diff = day === 0 ? 6 : day - 1; // Monday = 0
    const monday = new Date(now);
    monday.setDate(now.getDate() - diff);
    return { date_from: toLocalDateString(monday) };
  }
  // month
  return { date_from: toLocalDateString(new Date(now.getFullYear(), now.getMonth(), 1)) };
}
