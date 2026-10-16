import React from 'react';
import { BRAND_TAGLINE } from './brand';
import Icon from './icons';

/**
 * The thin bar above the page: a menu button on a phone (the rail is a drawer there), the page's title (the open conversation's name on the chat
 * page), the product's sub header in small text, whatever buttons the page asks for (the chat's trace button), and the help button every page has
 * (the help panel was reachable only from the account menu after the redesign). It replaces the nav bar plus the
 * 110px hero banner, which together took a fifth of a phone's height.
 */
const TopBar: React.FC<{ title: string; isMobile: boolean; onOpenMenu: () => void; actions?: React.ReactNode; onHelp?: () => void }> = ({ title, isMobile, onOpenMenu, actions, onHelp }) => (
  <header className="app-topbar">
    {isMobile && (
      <button type="button" className="topbar-menu" onClick={onOpenMenu} aria-label="Open menu"><Icon name="menu" size={20} /></button>
    )}
    <h1 className="topbar-title">{title}</h1>
    <span className="topbar-sub">{BRAND_TAGLINE}</span>
    <div className="topbar-actions">
      {actions}
      {onHelp && (
        <button type="button" className="topbar-btn" onClick={onHelp} aria-label="Help" title="Help"><Icon name="help" size={18} /></button>
      )}
    </div>
  </header>
);

export default TopBar;
