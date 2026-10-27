/**
 * Whether an assistant message is one of the approval cards the server writes (backend/services/agent_workflow.py, WRITE_ACTIONS and the tool agent's propose_* steps): the "Approval Required"
 * envelope with its "Please Approve ..." footer, which covers the pull request, issue, calendar, email, database, commit, merge and older multi-file plan cards alike.
 * The server decides what a reply approves by reading the card in the message before it, so a button only has to send the same word a person would have typed.
 */
export const APPROVE_TEXT = 'Approve';
export const REJECT_TEXT = 'Reject';

export function isApprovalCard(text: string | undefined | null): boolean {
  const content = text ?? '';
  return content.includes('Approval Required') && /Please Approve\b/.test(content);
}

/** Only the newest message can be answered: the server reads the card two messages back, so an older card's buttons would approve nothing. */
export function showsApprovalButtons(text: string | undefined | null, isLatest: boolean, loading: boolean): boolean {
  return isLatest && !loading && isApprovalCard(text);
}
