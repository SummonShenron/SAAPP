// Who is shown the Self Service page in the navigation (2026-11-05). Self Service is where documents are added to a knowledge base, so it is only useful to someone who may add them: a guest saw a page of jargon,
// a locked upload and an empty list. The rule is the page's own (pages/SelfService.tsx hasIngestPermission): a global admin, or someone with the Ingesters role on a knowledge base they can use. This only
// decides what to SHOW; the server enforces who may add or delete documents, so a wrong answer here can never expose anything. The page itself is still reachable by its address.

export const GLOBAL_ADMINS = 'Global_Admins';

/** Whether to show the Self Service link: an admin, or a member of "<knowledge base> Ingesters" for at least one of the knowledge bases the person may use. Pure. */
export function canUseSelfService(groups: readonly string[] | null | undefined, knowledgeBaseIds: readonly string[] | null | undefined): boolean {
  if (!Array.isArray(groups) || groups.length === 0) return false;
  if (groups.includes(GLOBAL_ADMINS)) return true;
  return (Array.isArray(knowledgeBaseIds) ? knowledgeBaseIds : []).some(id => typeof id === 'string' && id !== '' && groups.includes(`${id} Ingesters`));
}
