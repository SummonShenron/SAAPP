import React, { useEffect, useState } from "react";
import { getSonicProfile, type SonicProfile } from "../api";

/** The "About Sonic" topic in the help panel: Sonic's character profile, served by the backend so there is
 *  exactly one place it is written (backend/components/sonic_profile.py). Uses the help panel's own list styling. */
const SonicProfileCard: React.FC = () => {
  const [profile, setProfile] = useState<SonicProfile | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getSonicProfile()
      .then((data) => { if (!cancelled) setProfile(data); })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, []);

  if (failed) return <p>Couldn't load Sonic's profile right now.</p>;
  if (!profile) return <p>Loading…</p>;

  return (
    <>
      <p><strong>{profile.tagline}</strong></p>
      <p>Into:</p>
      <ul>{profile.into.map((item) => <li key={item}>{item}</li>)}</ul>
      <p>Prefers:</p>
      <ul>{profile.prefers.map((item) => <li key={item}>{item}</li>)}</ul>
      <p>Won't:</p>
      <ul>{profile.wont.map((item) => <li key={item}>{item}</li>)}</ul>
      <p>Voice: {profile.voice}</p>
      <p><em>{profile.note}</em></p>
    </>
  );
};

export default SonicProfileCard;
