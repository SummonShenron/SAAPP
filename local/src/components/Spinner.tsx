import React from 'react';
import './__styles__/Spinner.css';

/** The app's own loading spinner: a plain ring drawn in CSS (no image), in the current text colour unless the theme sets --spinner-color. */
const Spinner: React.FC<{ size?: number; className?: string }> = ({ size = 48, className }) => (
  <span className={`app-spinner${className ? ` ${className}` : ''}`} style={{ width: size, height: size }} aria-hidden="true" />
);

export default Spinner;
