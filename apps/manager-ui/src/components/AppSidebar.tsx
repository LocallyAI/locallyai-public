import { Link, useRouterState } from "@tanstack/react-router";
import {
  LayoutDashboard,
  FileText,
  MessageSquare,
  Users,
  ScrollText,
  Activity,
  Download,
  Cpu,
  RefreshCw,
  ShieldCheck,
  Lock,
  LogOut,
  Scale,
  GitCompareArrows,
} from "lucide-react";
import { clearAdminKey } from "@/lib/auth";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarHeader,
  SidebarFooter,
} from "@/components/ui/sidebar";

const items = [
  { title: "Dashboard", url: "/", icon: LayoutDashboard },
  { title: "Documents", url: "/documents", icon: FileText },
  { title: "Compare", url: "/compare", icon: GitCompareArrows },
  { title: "Query", url: "/query", icon: MessageSquare },
  { title: "Users", url: "/users", icon: Users },
  { title: "Conflicts", url: "/conflicts", icon: Scale },
  { title: "Audit Log", url: "/audit", icon: ScrollText },
  { title: "Compliance", url: "/compliance", icon: ShieldCheck },
  { title: "System", url: "/system", icon: Activity },
  { title: "Models", url: "/models", icon: Cpu },
  { title: "Updates", url: "/updates", icon: RefreshCw },
  { title: "Client Apps", url: "/downloads", icon: Download },
];

export function AppSidebar() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="border-b border-sidebar-border">
        <div className="flex items-center gap-2.5 px-2 py-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary/15 border border-primary/30">
            <Lock className="h-4 w-4 text-primary" />
          </div>
          <div className="flex flex-col leading-tight group-data-[collapsible=icon]:hidden">
            <span className="text-sm font-semibold tracking-tight text-sidebar-foreground">
              LocallyAI
            </span>
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
              On-Premises
            </span>
          </div>
        </div>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel className="text-[10px] uppercase tracking-wider">
            Workspace
          </SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {items.map((item) => {
                const active =
                  item.url === "/"
                    ? pathname === "/"
                    : pathname.startsWith(item.url);
                return (
                  <SidebarMenuItem key={item.title}>
                    <SidebarMenuButton asChild isActive={active}>
                      <Link to={item.url} className="flex items-center gap-2.5">
                        <item.icon className="h-4 w-4 shrink-0" />
                        <span>{item.title}</span>
                      </Link>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                );
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter className="border-t border-sidebar-border">
        <div className="px-2 py-2 group-data-[collapsible=icon]:hidden">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="status-dot bg-success" />
            <span>Running locally</span>
          </div>
          <div className="mt-1 text-[10px] text-muted-foreground/70">
            No external connections
          </div>
          <button
            onClick={() => {
              clearAdminKey();
              window.location.reload();
            }}
            className="mt-3 flex w-full items-center gap-2 rounded-md border border-border bg-secondary px-2 py-1.5 text-[11px] font-medium text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <LogOut className="h-3 w-3" />
            Sign out
          </button>
        </div>
      </SidebarFooter>
    </Sidebar>
  );
}
