import React, { useState } from 'react';

/**
 * A short list of plain-text items (the profile's interests, and topics to leave alone): type one and press Enter or comma to add it, click the x on a
 * chip to remove it. It only manages the list; the server decides what is acceptable and says why when it is not.
 */
const TagInput: React.FC<{
  id: string;
  label: string;
  values: string[];
  onChange: (next: string[]) => void;
  max: number;
  maxLength: number;
  placeholder?: string;
}> = ({ id, label, values, onChange, max, maxLength, placeholder }) => {
  const [draft, setDraft] = useState('');

  const add = (raw: string) => {
    const text = raw.replace(/\s+/g, ' ').trim();
    if (!text || values.length >= max || values.some(v => v.toLowerCase() === text.toLowerCase())) { setDraft(''); return; }
    onChange([...values, text]);
    setDraft('');
  };

  return (
    <div className="tag-input">
      {values.length > 0 && (
        <ul className="tag-input-chips" aria-label={label}>
          {values.map(v => (
            <li key={v} className="tag-input-chip">
              {v}
              <button type="button" aria-label={`Remove ${v}`} onClick={() => onChange(values.filter(x => x !== v))}>×</button>
            </li>
          ))}
        </ul>
      )}
      <input
        id={id}
        className="account-input"
        type="text"
        value={draft}
        maxLength={maxLength}
        placeholder={values.length >= max ? `That's the most (${max})` : placeholder}
        disabled={values.length >= max}
        onChange={e => setDraft(e.target.value.replace(',', ''))}
        onKeyDown={e => {
          if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(draft); }
          else if (e.key === 'Backspace' && !draft && values.length) onChange(values.slice(0, -1));
        }}
        onBlur={() => add(draft)}
        aria-label={`Add to ${label}`}
      />
    </div>
  );
};

export default TagInput;
