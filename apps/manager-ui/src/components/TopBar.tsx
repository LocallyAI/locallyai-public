import { useEffect, useState } from "react";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { ShieldCheck, WifiOff, Building2 } from "lucide-react";
import { getBranding, type BrandingResponse } from "@/lib/api";
import { SettingsToggle } from "@/components/SettingsToggle";

interface TopBarProps {
  title: string;
  description?: string;
}

export function TopBar({ title, description }: TopBarProps) {
  // Branding is unauthenticated and the same for every page; cache it
  // module-level so navigating between routes doesn't re-fetch.
  const [branding, setBranding] = useState<BrandingResponse | null>(_brandingCache);
  useEffect(() => {
    if (_brandingCache) return;
    getBranding()
      .then((b) => { _brandingCache = b; setBranding(b); })
      .catch(() => { /* legacy / unreachable — header degrades gracefully */ });
  }, []);

  return (
    <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-3 border-b border-border bg-background/80 px-4 backdrop-blur-sm">
      <SidebarTrigger className="-ms-1" />
      <div className="h-5 w-px bg-border" />
      <div className="flex flex-col leading-tight">
        <h1 className="text-sm font-semibold text-foreground">{title}</h1>
        {description && (
          <p className="text-xs text-muted-foreground">{description}</p>
        )}
      </div>
      <div className="ml-auto flex min-w-0 items-center gap-2">
        {branding && (
          <div
            className="hidden shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border border-primary/30 bg-primary/5 px-2.5 py-1 text-xs font-medium text-foreground md:flex"
            title={`Connected to ${branding.firm_name}'s LocallyAI deployment at ${branding.office_host || branding.deployment_id}. ${branding.isolation_statement}`}
          >
            <Building2 className="h-3.5 w-3.5 shrink-0 text-primary" />
            <span className="shrink-0">Firm:</span>
            <span className="block max-w-[180px] truncate text-primary">{branding.firm_name}</span>
          </div>
        )}
        <div className="hidden shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border border-border bg-card px-2.5 py-1 text-xs text-muted-foreground lg:flex">
          <WifiOff className="h-3 w-3 shrink-0" />
          <span>Air-gapped</span>
        </div>
        <div className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border border-success/30 bg-success/10 px-2.5 py-1 text-xs text-foreground">
          <span className="status-dot bg-success animate-pulse shrink-0" />
          <span>Operational</span>
        </div>
        <div className="hidden shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border border-border bg-card px-2.5 py-1 text-xs text-muted-foreground xl:flex">
          <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-primary" />
          <span>Mac Studio · M2 Ultra</span>
        </div>
        <SettingsToggle />
      </div>
    </header>
  );
}

// Module-level branding cache. /v1/branding never changes during a
// session (firm name is set once at install) so a single fetch covers
// every page. Resets on full reload.
let _brandingCache: BrandingResponse | null = null;
