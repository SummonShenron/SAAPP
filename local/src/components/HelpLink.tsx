import React from 'react';

/** A button inside the help text that closes the panel and goes to a page ("Open Tidy up"). */
const HelpLink: React.FC<{ go: (path: string) => void; path: string; children: React.ReactNode }> = ({ go, path, children }) => (
  <button type="button" className="help-link" onClick={() => go(path)}>{children}</button>
);

export default HelpLink;
