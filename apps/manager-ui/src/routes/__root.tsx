import { Outlet, createRootRoute, HeadContent, Scripts } from "@tanstack/react-router";
import { SidebarProvider } from "@/components/ui/sidebar";
import { AppSidebar } from "@/components/AppSidebar";
import { LoginGate } from "@/components/LoginGate";

import appCss from "../styles.css?url";

export const Route = createRootRoute({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "LocallyAI — On-Premises AI for Regulated Firms" },
      { name: "description", content: "LocallyAI: secure on-premises AI platform for law firms and financial services. Runs entirely on local hardware." },
    ],
    links: [
      { rel: "stylesheet", href: appCss },
      { rel: "preconnect", href: "https://fonts.googleapis.com" },
      { rel: "preconnect", href: "https://fonts.gstatic.com", crossOrigin: "anonymous" },
      { rel: "stylesheet", href: "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" },
    ],
  }),
  shellComponent: RootShell,
  component: RootComponent,
  notFoundComponent: () => (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="text-center">
        <h1 className="text-6xl font-bold">404</h1>
        <p className="mt-2 text-muted-foreground">Page not found</p>
        <a href="/" className="mt-4 inline-block text-primary hover:underline">Return to dashboard</a>
      </div>
    </div>
  ),
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
      <SidebarProvider>
        <div className="flex min-h-screen w-full bg-background">
          <AppSidebar />
          <div className="flex min-w-0 flex-1 flex-col">
            <Outlet />
          </div>
        </div>
      </SidebarProvider>
    </LoginGate>
  );
}
