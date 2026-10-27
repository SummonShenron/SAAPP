import { useState } from 'react';
import { APPROVE_TEXT, REJECT_TEXT } from '../approvalCard';
import './__styles__/ApprovalButtons.css';

interface ApprovalButtonsProps {
  /** Sends the word as the person's next message, exactly as if they had typed it. */
  onAnswer: (text: string) => void;
}

/**
 * Approve and Reject for an approval card in the chat. Pressing one sends the same word a person would have typed, so the server's reading of the card is unchanged; the buttons go away once
 * one is pressed (the next message is the answer) and never show on an older card. Changing the details is still done by typing what to change.
 */
export default function ApprovalButtons({ onAnswer }: ApprovalButtonsProps) {
  const [answered, setAnswered] = useState(false);
  const answer = (text: string) => {
    setAnswered(true);
    onAnswer(text);
  };
  return (
    <div className="approval-actions" role="group" aria-label="Answer this approval">
      <button type="button" className="approval-btn approval-btn-primary" disabled={answered} onClick={() => answer(APPROVE_TEXT)}>Approve</button>
      <button type="button" className="approval-btn" disabled={answered} onClick={() => answer(REJECT_TEXT)}>Reject</button>
      <span className="approval-note">Or type what to change.</span>
    </div>
  );
}
