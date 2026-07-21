// 1. Centralized Security Directory Map.
const AFFILIATE_QUESTION_POOLS: Record<string, string[]> = {
  'Affiliate_A': [
    "Who is Sonic?",
    "Tell me about Sonic Adventure 2.",
    "Is there any data regarding Shadow?",
    "What are the Chaos Emeralds?",
    "Who is Dr. Eggman?"
  ],
  'Affiliate_B': [
    "Who is Goku?",
    "What are the Dragon Balls?",
    "Tell me about the Saiyan race.",
    "Who is Vegeta?",
    "What is a Senzu Bean?"
  ],
  'Affiliate_C': [
    "Tell me about Jack and his work.",
    "What is the Story of the Sonic Assistant",
    "Tell me why Jack built the Sonic Assistant?"
  ]
};

/**
 * Dynamically fetches example questions strictly scoped to the user's authorized affiliates.
 */
export async function getDynamicExampleQuestions(
  allowedAffiliates: string[], // Pass authorizations instead of usernames
  affiliate: string
): Promise<string[]> {
  // Retain production async signature & simulate engine latency
  await new Promise((resolve) => setTimeout(resolve, 250));

  if (!allowedAffiliates || allowedAffiliates.length === 0) return [];

  try {
    // Scenario 1: Cross-Domain Mixed Scope ("All")
    if (affiliate === 'All') {
      // Build a combined pool using ONLY the affiliates this session has clearance for
      const authorizedPool = allowedAffiliates
        .flatMap(aff => AFFILIATE_QUESTION_POOLS[aff] || []);

      const shuffled = [...authorizedPool].sort(() => 0.5 - Math.random());
      return shuffled.slice(0, 3); 
    }

    // Scenario 2: Target Isolated Tenant Scope
    const targetedPool = AFFILIATE_QUESTION_POOLS[affiliate] || [];
    return targetedPool.slice(0, 3);

  } catch (error) {
    console.error("Failed to map affiliate directory vectors to question pools:", error);
    return []; // Resilient fallback boundary
  }
}