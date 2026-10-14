import { useEffect, useMemo } from 'react';
import { useLocation } from 'react-router-dom';
import { realNow } from '@/os/TimeService';
import { IcOffline, IcWarning } from '../res/icons';
import { attachFrame } from '../host';
import { useNanoMuseStore } from '../state';
import { useNanoMuseGestures } from '../hooks/useNanoMuseGestures';
import { fmt, useNanoMuseStrings } from '../res/strings';

/**
 * The nanoMuse web app, full screen. `?thread=` and `?tab=` on this route are forwarded to
 * the web app's own deep links, which is how a tapped notification lands on the right chat.
 * On a hosted showcase session the web app comes up lite (`?ui=lite`): the chat alone, no
 * first-run setup — the rest of it stays behind the avatar.
 */
export default function MusePage() {
  const serverUrl = useNanoMuseStore((s) => s.serverUrl);
  const token = useNanoMuseStore((s) => s.token);
  const demo = useNanoMuseStore((s) => s.demo);
  const link = useNanoMuseStore((s) => s.link);
  const { bindTap, go } = useNanoMuseGestures();
  const location = useLocation();
  const s = useNanoMuseStrings();

  // first launch: nothing configured yet → setup
  useEffect(() => {
    if (!serverUrl) go('setup.open', {}, { mode: 'replace' });
  }, [serverUrl, go]);

  const src = useMemo(() => {
    if (!serverUrl) return '';
    const params = new URLSearchParams(location.search);
    const url = new URL(serverUrl);
    url.pathname = '/';
    // the web app stores the token on first load and removes it from its own URL; it travels
    // in the fragment (web/src/api.ts tokenFromLink), which never reaches the gateway or a log
    if (token) url.hash = `token=${encodeURIComponent(token)}`;
    const thread = params.get('thread');
    const tab = params.get('tab');
    if (thread) url.searchParams.set('thread', thread);
    if (tab) url.searchParams.set('tab', tab);
    if (demo) url.searchParams.set('ui', 'lite');
    return url.toString();
  }, [serverUrl, token, demo, location.search]);

  const trouble = link === 'unauthorized' || link === 'unreachable';
  // a hosted showcase session that has run out: the server behind serverUrl is gone for good
  const demoOver = demo !== null && trouble && (realNow() / 1000 > demo.expiresAt || link === 'unauthorized');
  const webTheme = useNanoMuseStore((s) => s.webTheme);
  const setWebTheme = useNanoMuseStore((s) => s.setWebTheme);
  // a new frame comes up light (the web app's default) until it reports otherwise
  useEffect(() => {
    setWebTheme('light');
  }, [src, setWebTheme]);
  const dark = webTheme === 'dark';
  const palette = dark ? WEB_APP_DARK : WEB_APP_LIGHT;

  return (
    <div
      className="h-full w-full flex flex-col pt-10"
      style={{ background: palette.bg }}
      data-status-bar-foreground={dark ? 'light' : 'dark'}
    >
      <div className="flex-1 relative min-h-0">
        {src && (
          <iframe
            key={src}
            ref={attachFrame}
            src={src}
            title="nanoMuse"
            className="absolute inset-0 w-full h-full border-0"
            style={{ background: palette.bg }}
            allow="clipboard-write"
          />
        )}
        {trouble && (
          <div className="absolute inset-x-4 top-3 z-10 rounded-2xl bg-app-surface border border-app-border shadow-lg p-3.5 flex items-start gap-3">
            <div className="mt-0.5 text-amber-600 shrink-0">
              {link === 'unauthorized' ? <IcWarning size={20} /> : <IcOffline size={20} />}
            </div>
            <div className="flex-1 min-w-0 text-[13px] leading-snug text-app-text">
              {demoOver ? s.muse_demo_over : link === 'unauthorized' ? s.muse_refused : fmt(s.muse_unreachable, serverUrl)}
            </div>
            <button
              type="button"
              {...bindTap('setup.open')}
              className="shrink-0 rounded-xl bg-app-primary text-app-on-primary text-[12.5px] font-semibold px-3 py-1.5"
            >
              {demoOver ? s.muse_new : s.muse_change_server}
            </button>
          </div>
        )}
      </div>
      {/* the simulator draws its gesture bar over the last 16 px; keep the web app's tab bar clear of it */}
      <div className="h-4 shrink-0" style={{ background: palette.bg }} aria-hidden="true" />
    </div>
  );
}

// The web app's own palette (web/src/index.css: --om-bg, --om-surface). It picks light or dark
// by its own setting, not the simulator's theme, and tells its host which — the strips above
// and below its frame follow that, the way the Android app's status and gesture bars do.
const WEB_APP_LIGHT = { bg: '#ffffff' };
const WEB_APP_DARK = { bg: '#181819' };
