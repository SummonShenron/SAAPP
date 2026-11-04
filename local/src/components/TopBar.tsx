import React from 'react';
import { BRAND_TAGLINE } from './brand';
import Icon from './icons';
import { useHelpMode } from './HelpMode';

/**
 * The thin bar above the page: a menu button on a phone (the rail is a drawer there), the page's title (the open conversation's name on the chat
 * page), the product's sub header in small text, whatever buttons the page asks for (the chat's trace button), and the Help Mode button every page has: it turns on a mode where pressing anything
 * explains it (components/HelpMode.tsx). The long help guide is under the user chip's menu. It replaces the nav bar plus the
 * 110px hero banner, which together took a fifth of a phone's height.
 */
const TopBar: React.FC<{ title: string; isMobile: boolean; onOpenMenu: () => void; actions?: React.ReactNode }> = ({ title, isMobile, onOpenMenu, actions }) => {
  const help = useHelpMode();
  return (
  <header className="app-topbar">
    {isMobile && (
      <button type="button" className="topbar-menu" onClick={onOpenMenu} aria-label="Open menu" data-help="topbar.menu"><Icon name="menu" size={20} /></button>
    )}
    <h1 className="topbar-title" data-help="topbar.title">{title}</h1>
    <span className="topbar-sub" data-help="topbar.tagline">{BRAND_TAGLINE}</span>
    <div className="topbar-actions">
      {actions}
      <button
        type="button" className={`topbar-btn${help.active ? ' help-active' : ''}`} onClick={help.toggle} aria-label="Help Mode" aria-pressed={help.active} data-help-ui
        title={help.active ? 'Leave Help Mode' : 'Help Mode: press anything to see what it does'}
      >
        <Icon name="help" size={18} />
      </button>
    </div>
  </header>
  );
};

export default TopBar;
