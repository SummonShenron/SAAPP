import { getExampleQuestions } from '../api';

// 1. Centralized Security Directory Map.
const AFFILIATE_QUESTION_POOLS: Record<string, string[]> = {
  'Affiliate_A': [
    "Who is Sonic?",
    "Write a script comparing the speed of Sonic and Shadow.",
    "Tell me about Sonic Adventure 2.",
    "Is there any data regarding Shadow?",
    "What are the Chaos Emeralds?",
    "Who is Dr. Eggman?"
  ],
  'Affiliate_B': [
    "Write a script that compares the power levels of various Dragon Ball characters.",
    "Who is Goku?",
    "What are the Dragon Balls?",
    "Tell me about the Saiyan race.",
    "Who is Vegeta?",
    "What is a Senzu Bean?"
  ],
  'Affiliate_C': [
    "Explain the agent_workflow.py file and its role in the Sonic Assistant's multi-agent workflow.",
    "What is the Story of the Sonic Assistant",
    "How does Sonic Assistant's memory system work?",
    "Who is Jack Harper?",
    "What was changed in the last pull request?",
    "What are the different routing strategies leveraged by the Sonic Assistant?",
    "How does the Sonic Assistant enforce data isolation?"
  ],
  'Affiliate_D': [
    "How do I book a session?",
    "Where is Trainer's Edge, and how can I get in contact with Madison?",
    "What is the proper deadlift form?",
    "How can I get in contact with Coach Madison?",
    "Can you explain why it is worth investing in personal training?",
    "Explain progressive overload in strength training."
  ],
};

function shuffledCopy<T>(items: readonly T[]): T[] {
  const shuffled = [...items];
  for (let index = shuffled.length - 1; index > 0; index -= 1) {
    const randomIndex = Math.floor(Math.random() * (index + 1));
    [shuffled[index], shuffled[randomIndex]] = [shuffled[randomIndex], shuffled[index]];
  }
  return shuffled;
}

const CARD_LIMIT = 6;
// With "All" selected, questions about the person's own state lead and the demo pools only fill what is left.
const PERSONAL_LEAD = 4;

/** Questions about this person's own state (their documents, integrations, repo). Never throws: an empty list
 *  just means the screen falls back to the demo pools. */
async function fetchPersonalQuestions(affiliate: string): Promise<string[]> {
  try {
    return await getExampleQuestions(affiliate);
  } catch (error) {
    console.error("Failed to fetch personal example questions:", error);
    return [];
  }
}

/**
 * Example questions for the welcome screen, strictly scoped to the user's authorized affiliates.
 *
 * - A knowledge base with a demo pool (Affiliate_A to D) keeps showing that pool.
 * - A knowledge base without one, such as a user's own, shows questions about their own state, built by the server.
 * - "All" leads with those personal questions and fills the rest from the demo pools they have access to.
 */
export async function getDynamicExampleQuestions(
  allowedAffiliates: string[], // Pass authorizations instead of usernames
  affiliate: string
): Promise<string[]> {
  if (!allowedAffiliates || allowedAffiliates.length === 0) return [];

  try {
    // Scenario 1: Cross-Domain Mixed Scope ("All")
    if (affiliate === 'All') {
      // Build a combined pool using ONLY the affiliates this session has clearance for
      const authorizedPool = allowedAffiliates
        .flatMap(aff => AFFILIATE_QUESTION_POOLS[aff] || []);
      const personal = (await fetchPersonalQuestions(affiliate)).slice(0, PERSONAL_LEAD);
      const fill = shuffledCopy(authorizedPool).filter(q => !personal.includes(q));
      return [...personal, ...fill].slice(0, CARD_LIMIT);
    }

    // Scenario 2: Target Isolated Tenant Scope
    const targetedPool = AFFILIATE_QUESTION_POOLS[affiliate];
    if (targetedPool) return shuffledCopy(targetedPool).slice(0, CARD_LIMIT);

    // Scenario 3: a knowledge base with no demo pool (their own): questions about their own state
    return (await fetchPersonalQuestions(affiliate)).slice(0, CARD_LIMIT);

  } catch (error) {
    console.error("Failed to map affiliate directory vectors to question pools:", error);
    return []; // Resilient fallback boundary
  }
}