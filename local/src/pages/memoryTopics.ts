// The Memory page's topics (pages/Memory.tsx): the panel at its left lists "Coming up", "All memories" and one topic for each category the person has memories in; the list at its right shows the topic picked.
// Kept apart from React so the rules can be tested (memoryTopics.test.mjs).

/** The topic that shows the upcoming things instead of the saved memories. A category's own name is its topic; "all" is every memory. */
export const COMING_UP = '__coming_up';
export const ALL_TOPIC = 'all';

/** What a topic is called in the heading above the list: a category's name with a capital. */
export function topicLabel(topic: string): string {
  return topic ? topic.charAt(0).toUpperCase() + topic.slice(1) : topic;
}

/**
 * The topic that is really on show. A topic with nothing left in it (the last memory of a category forgotten, the last upcoming item removed) falls back to everything, so the page never shows an empty topic that is
 * no longer in the panel. Pure.
 */
export function activeTopicOf(topic: string, categories: readonly string[], comingUpItems: number): string {
  if (topic === COMING_UP) return comingUpItems > 0 ? COMING_UP : ALL_TOPIC;
  return topic === ALL_TOPIC || categories.includes(topic) ? topic : ALL_TOPIC;
}

/** The topics in the order the panel lists them: coming up (only when there are upcoming items), everything, then the categories as given. Pure. */
export function topicList(categories: readonly string[], comingUpItems: number): string[] {
  return [...(comingUpItems > 0 ? [COMING_UP] : []), ALL_TOPIC, ...categories];
}
