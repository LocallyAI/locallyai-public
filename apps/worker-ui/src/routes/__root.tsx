import { Outlet, Link, createRootRoute, HeadContent, Scripts } from "@tanstack/react-router";
import { LoginGate } from "@/components/locally/LoginGate";

import appCss from "../styles.css?url";

function NotFoundComponent() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1 className="text-7xl font-bold text-foreground">404</h1>
        <h2 className="mt-4 text-xl font-semibold text-foreground">Page not found</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          The page you're looking for doesn't exist or has been moved.
        </p>
        <div className="mt-6">
          <Link
            to="/"
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Go home
          </Link>
        </div>
      </div>
    </div>
  );
}

export const Route = createRootRoute({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "LocallyAI — Workspace" },
      { name: "description", content: "Secure on-premises AI workspace. All processing happens locally." },
      { property: "og:title", content: "LocallyAI Workspace" },
      { property: "og:description", content: "Secure on-premises AI workspace." },
      { property: "og:type", content: "website" },
    ],
    links: [
      { rel: "stylesheet", href: appCss },
      { rel: "preconnect", href: "https://fonts.googleapis.com" },
      { rel: "preconnect", href: "https://fonts.gstatic.com", crossOrigin: "anonymous" },
      {
        rel: "stylesheet",
        href: "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
      },
    ],
  }),
  shellComponent: RootShell,
  component: RootComponent,
  notFoundComponent: NotFoundComponent,
});

function RootShell({ children }: { children: React.ReactNode }) {
  // Build-time defaults per region. The inline first-paint script reads
  // localStorage and adjusts <html> attributes/classes BEFORE React
  // mounts — avoids flash of wrong theme or language. Runtime toggles
  // (lib/theme.ts, lib/i18n.ts) keep both in sync from there.
  const env = (import.meta as unknown as { env: { VITE_DEFAULT_LANG?: string } }).env;
  const initialLang = env?.VITE_DEFAULT_LANG === "ar" ? "ar" : "en";
  const initialDir  = initialLang === "ar" ? "rtl" : "ltr";
  const firstPaintScript = `
    (function(){
      try {
        var t = localStorage.getItem('locallyai_theme');
        var l = localStorage.getItem('locallyai_lang');
        var html = document.documentElement;
        var body = document.body;
        if (t === 'light') {
          html.classList.remove('dark');
          if (body) body.classList.remove('dark');
        } else {
          html.classList.add('dark');
          if (body) body.classList.add('dark');
        }
        if (l === 'ar' || l === 'en') {
          html.setAttribute('lang', l);
          html.setAttribute('dir', l === 'ar' ? 'rtl' : 'ltr');
        }
      } catch (e) { /* localStorage blocked — keep SSR defaults */ }
    })();
  `;
  return (
    <html lang={initialLang} dir={initialDir} className="dark">
      <head>
        <HeadContent />
        <script dangerouslySetInnerHTML={{ __html: firstPaintScript }} />
      </head>
      <body className="dark">
        {children}
        <Scripts />
      </body>
    </html>
  );
}

function RootComponent() {
  return (
    <LoginGate>
      <Outlet />
    </LoginGate>
  );
}
