/**
 * The sections of the Sonic Health page and how they are grouped into a two-level menu (Sonic Health used to show all fifteen as one flat row of tabs). Kept apart from React so the grouping and the
 * keyboard moves can be tested without a browser (sonicHealthTabs.test.mjs).
 *
 * A section's id is what the page's address, the remembered last section (`sonic-health-tab`) and Home's "open this" links already use, so none of them change: only where a section sits in the menu does.
 */
export const TABS = [
  { id: 'home', label: 'Home' },
  { id: 'usage', label: 'Usage' },
  { id: 'turns', label: 'Turns' },
  { id: 'budget', label: 'Budget' },
  { id: 'behavior', label: 'Behavior' },
  { id: 'goals', label: 'Goals' },
  { id: 'feedback', label: 'Feedback' },
  { id: 'knowledge', label: 'Knowledge graph' },
  { id: 'routines', label: 'Routines' },
  { id: 'safety', label: 'Safety' },
  { id: 'observations', label: 'Observations' },
  { id: 'shelf', label: 'Reading shelf' },
  { id: 'experiences', label: 'Its record' },
  { id: 'selfmodel', label: 'Self-model' },
  { id: 'suggestions', label: 'Suggestions' },
] as const;

export type TabId = typeof TABS[number]['id'];

export function isTab(value: string | null | undefined): value is TabId {
  return TABS.some(t => t.id === value);
}

export function labelOf(tab: TabId): string {
  return TABS.find(t => t.id === tab)?.label ?? tab;
}

export interface MenuGroup {
  id: string;
  label: string;
  tabs: readonly TabId[];
}

/** Home and Safety stand alone (a click opens them); the others open a menu. Every section is in exactly one group, in the order it is listed here. */
export const GROUPS: readonly MenuGroup[] = [
  { id: 'home', label: 'Home', tabs: ['home'] },
  { id: 'spend', label: 'Spend', tabs: ['usage', 'turns', 'budget'] },
  { id: 'quality', label: 'Quality', tabs: ['behavior', 'feedback', 'observations', 'goals'] },
  { id: 'know', label: 'Knowledge', tabs: ['knowledge', 'shelf', 'routines'] },
  { id: 'sonic', label: 'Sonic', tabs: ['experiences', 'selfmodel', 'suggestions'] },
  { id: 'safety', label: 'Safety', tabs: ['safety'] },
];

export function groupOf(tab: TabId): MenuGroup {
  return GROUPS.find(group => group.tabs.includes(tab)) ?? GROUPS[0];
}

export type Tone = 'info' | 'warn';
export interface Badge { text: string; tone: Tone; }
export type Badges = Partial<Record<TabId, Badge>>;

/**
 * What the closed group shows when something inside it needs a look. A group of one shows that section's own badge. A bigger group shows "!" when any section in it needs attention and a dot when
 * some only have news, so the bar still says where to look without opening anything; the menu then shows each section's own badge.
 */
export function groupBadge(group: MenuGroup, badges: Badges): Badge | null {
  const flagged = group.tabs.map(tab => badges[tab]).filter((badge): badge is Badge => !!badge);
  if (flagged.length === 0) return null;
  if (group.tabs.length === 1) return flagged[0];
  return flagged.some(badge => badge.tone === 'warn') ? { text: '!', tone: 'warn' } : { text: '•', tone: 'info' };
}

/** Which top-level item the arrow keys move to: left and right wrap round, Home and End go to the ends. Null for any other key. */
export function nextTop(index: number, key: string, count: number): number | null {
  if (count <= 0) return null;
  if (key === 'ArrowRight') return (index + 1) % count;
  if (key === 'ArrowLeft') return (index - 1 + count) % count;
  if (key === 'Home') return 0;
  if (key === 'End') return count - 1;
  return null;
}

/** Which entry of an open menu the arrow keys move to: up and down wrap round, Home and End go to the ends. Null for any other key. */
export function nextEntry(index: number, key: string, count: number): number | null {
  if (count <= 0) return null;
  if (key === 'ArrowDown') return (index + 1) % count;
  if (key === 'ArrowUp') return (index - 1 + count) % count;
  if (key === 'Home') return 0;
  if (key === 'End') return count - 1;
  return null;
}
