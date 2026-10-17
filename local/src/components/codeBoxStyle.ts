/** The frame of a fenced code block in a reply: shared by the highlighted version (HighlightedCode.tsx) and the plain placeholder shown while that loads. */
export const CODE_BOX_STYLE = {
  margin: 0,
  borderRadius: '8px',
  fontSize: '0.85rem',
  background: 'var(--code-bg)',
  border: '1px solid var(--code-border)',
} as const;
