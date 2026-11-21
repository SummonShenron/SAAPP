import React, { useEffect, useState } from 'react';
import { api } from '../api';
import { ALERT_POLL_MS, alertsOf, headline, sinceText, type ProviderAlertItem } from './providerAlertLogic';
import './__styles__/ProviderAlert.css';

/**
 * A strip across the top of every page, for an admin only, while a model provider is refusing Sonic's API key (backend/utils/provider_health.py): what is wrong, for how long, and what to do. It shows nothing when
 * every key is accepted. It is the point of the thing that an expired key can no longer go unseen for days while the work quietly runs on a fallback that costs several times more.
 *
 * The alerts come inside the admin's budget view (`provider_alerts`, sent whatever the budget says), asked for on arrival and then once a minute, so the strip goes soon after the key is fixed. A failed ask changes
 * nothing on screen: it keeps what it last knew.
 */
const ProviderAlert: React.FC<{ enabled: boolean }> = ({ enabled }) => {
  const [alerts, setAlerts] = useState<ProviderAlertItem[]>([]);

  useEffect(() => {
    if (!enabled) return undefined;          // not an admin: nothing is asked (and below, nothing is shown)
    let cancelled = false;
    const ask = () => {
      api.getBudget().then(view => { if (!cancelled) setAlerts(alertsOf(view)); }).catch(() => { /* keep what it last knew */ });
    };
    ask();
    const timer = window.setInterval(ask, ALERT_POLL_MS);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [enabled]);

  if (!enabled || alerts.length === 0) return null;
  return (
    <div className="provider-alert" role="alert">
      <div className="provider-alert-head">
        <span className="provider-alert-dot" aria-hidden="true" />
        <strong>{headline(alerts)}</strong>
        <span className="provider-alert-since">{sinceText(alerts[0].since)}</span>
      </div>
      {alerts.map(alert => <p key={alert.provider} className="provider-alert-text">{alert.message}</p>)}
    </div>
  );
};

export default ProviderAlert;
