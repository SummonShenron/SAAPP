// The help panel's topics: which there are, what they are called, and who sees them. The words themselves are in helpContent.tsx. Plain data with no React
// in it, so it can be tested on its own (helpTopics.test.mjs, run from local/ with `node --test src/components/helpTopics.test.mjs`).

export type HelpAudience = 'everyone' | 'signedIn';

export interface HelpTopicMeta {
  id: string;
  /** Short enough to sit on one line in the list on the left. */
  title: string;
  group: string;
  /** "signedIn": about something a guest does not have (memory, Google, Tidy up), so a guest does not see it. */
  audience: HelpAudience;
}

/** The order the groups appear in. The last one is for people who want to see how it works, and is folded away until they ask. */
export const GROUPS = ['Getting started', 'Your files and accounts', 'You and Sonic', 'Privacy and control', 'For developers'] as const;
export const DEVELOPER_GROUP = 'For developers';

export const TOPICS: HelpTopicMeta[] = [
  { id: 'start', title: 'What Sonic can do', group: 'Getting started', audience: 'everyone' },
  { id: 'try', title: 'Things to try', group: 'Getting started', audience: 'everyone' },

  { id: 'library', title: 'Your document library', group: 'Your files and accounts', audience: 'signedIn' },
  { id: 'attach', title: 'Photos, files and spreadsheets', group: 'Your files and accounts', audience: 'everyone' },
  { id: 'google', title: 'Email, calendar and Google', group: 'Your files and accounts', audience: 'signedIn' },
  { id: 'tidy', title: 'Tidy up a folder', group: 'Your files and accounts', audience: 'signedIn' },

  { id: 'memory', title: 'What Sonic remembers', group: 'You and Sonic', audience: 'signedIn' },
  { id: 'profile', title: 'Telling Sonic about yourself', group: 'You and Sonic', audience: 'signedIn' },
  { id: 'about-sonic', title: 'About Sonic', group: 'You and Sonic', audience: 'everyone' },

  { id: 'privacy', title: 'Your privacy and your data', group: 'Privacy and control', audience: 'everyone' },
  { id: 'conversations', title: 'Your conversations', group: 'Privacy and control', audience: 'everyone' },
  { id: 'menu', title: 'The "…" menu in the chat box', group: 'Privacy and control', audience: 'everyone' },
  { id: 'trouble', title: 'If something goes wrong', group: 'Privacy and control', audience: 'everyone' },

  { id: 'dev-github', title: 'GitHub tools', group: DEVELOPER_GROUP, audience: 'everyone' },
  { id: 'dev-prs', title: 'Pull requests and issues', group: DEVELOPER_GROUP, audience: 'everyone' },
  { id: 'dev-sandbox', title: 'Code sandbox', group: DEVELOPER_GROUP, audience: 'everyone' },
  { id: 'dev-folder', title: 'Connect a code folder', group: DEVELOPER_GROUP, audience: 'everyone' },
  { id: 'dev-modes', title: 'Deep Thinking and Target Repo', group: DEVELOPER_GROUP, audience: 'everyone' },
  { id: 'dev-access', title: 'Who can see what', group: DEVELOPER_GROUP, audience: 'everyone' },
  { id: 'dev-built', title: 'How it\'s built', group: DEVELOPER_GROUP, audience: 'everyone' },
];

/** The topics a person sees, in order: a guest does not see what needs an account. */
export function visibleTopics(guest: boolean): HelpTopicMeta[] {
  return TOPICS.filter(topic => !guest || topic.audience === 'everyone');
}

/** The visible topics grouped in display order, leaving out any group that has nothing for this person. */
export function groupedTopics(guest: boolean): { group: string; topics: HelpTopicMeta[] }[] {
  const visible = visibleTopics(guest);
  return GROUPS.map(group => ({ group, topics: visible.filter(topic => topic.group === group) })).filter(entry => entry.topics.length > 0);
}
