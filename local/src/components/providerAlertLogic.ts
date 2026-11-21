// What an admin is told when a model provider is refusing Sonic's API key (backend/utils/provider_health.py, 2026-11-20). The server sends the alerts inside the admin's budget view (`provider_alerts`), whatever the
// budget says. No React in here, so it can be tested (providerAlert.test.mjs).
//
// WHY. An expired Anthropic key made every Claude call answer 401 for three days; the app fell back to Gemini without a word, the same work cost about four times as much, and the first sign was a crash.

export interface ProviderAlertItem {
  /** "claude" or "openai". */
  provider: string;
  label: string;
  /** When the first refusal this run of the server saw, and the latest (ISO times). */
  since: string;
  last: string;
  failures: number;
  /** The server's words: what it means and what to do. */
  message: string;
}

/** How often the page asks again (only an admin's page asks at all), so the alert is gone soon after the key is fixed. */
export const ALERT_POLL_MS = 60_000;

/** The alerts in whatever the server sent: a list of well-formed items and nothing else (an old server with no field, a surprise, or a half-made item are all just "none"). Pure. */
export function alertsOf(view: unknown): ProviderAlertItem[] {
  const list = (view as { provider_alerts?: unknown } | null | undefined)?.provider_alerts;
  if (!Array.isArray(list)) return [];
  return list.filter((item): item is ProviderAlertItem => {
    const row = item as Partial<ProviderAlertItem> | null;
    return !!row && typeof row.provider === 'string' && typeof row.label === 'string' && typeof row.message === 'string' && typeof row.since === 'string';
  });
}

/** The one line that says what is wrong. Pure. */
export function headline(alerts: readonly ProviderAlertItem[]): string {
  if (alerts.length === 0) return '';
  if (alerts.length === 1) return `${alerts[0].label} is rejecting Sonic's API key`;
  return `${alerts.length} model providers are rejecting Sonic's API keys (${alerts.map(a => a.label).join(', ')})`;
}

/** How long it has been going on, in words: "since just now", "for 5 minutes", "for 3 days". An unreadable time says nothing. Pure. */
export function sinceText(iso: string, now: number = Date.now()): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return '';
  const minutes = Math.max(0, Math.floor((now - then) / 60_000));
  if (minutes < 1) return 'since just now';
  if (minutes < 60) return `for ${minutes} minute${minutes === 1 ? '' : 's'}`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `for ${hours} hour${hours === 1 ? '' : 's'}`;
  const days = Math.floor(hours / 24);
  return `for ${days} days`;
}
