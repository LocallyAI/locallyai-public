import { Plus, MessageSquare, Search, Clock, Shield, LogOut, Pencil } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n";
import { SettingsToggle } from "./SettingsToggle";

export type Conversation = { id: string; title: string; date: string };

interface Props {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string | null) => void;
  onNew: () => void;
  onRename?: (id: string, title: string) => void;
  userName?: string | null;
  onSignOut?: () => void;
}

function initialsFor(name: string | null | undefined): string {
  if (!name) return "—";
  const parts = name.trim().split(/\s+/).slice(0, 2);
  return parts.map((p) => p.charAt(0).toUpperCase()).join("") || name.charAt(0).toUpperCase();
}

export function Sidebar({ conversations, activeId, onSelect, onNew, onRename, userName, onSignOut }: Props) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<string>("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (editingId && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editingId]);

  const beginEdit = (id: string, current: string) => {
    setEditingId(id);
    setDraft(current);
  };
  const commit = () => {
    if (editingId && onRename) {
      const next = draft.trim();
      if (next.length > 0 && next.length <= 120) onRename(editingId, next);
    }
    setEditingId(null);
    setDraft("");
  };
  const cancel = () => {
    setEditingId(null);
    setDraft("");
  };

  return (
    <aside className="flex h-screen w-72 shrink-0 flex-col border-e border-sidebar-border bg-sidebar text-sidebar-foreground">
      <div className="flex items-center gap-2.5 px-5 pt-5 pb-4">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-foreground/95">
          <div className="h-2 w-2 rounded-sm bg-background" />
        </div>
        <div className="flex flex-col leading-tight">
          <span className="text-[13px] font-semibold tracking-tight text-foreground">{t("app.name")}</span>
          <span className="text-[11px] text-muted-foreground">{t("app.workspace")}</span>
        </div>
      </div>

      <div className="px-3">
        <button
          onClick={onNew}
          className="flex w-full items-center gap-2 rounded-md border border-border/70 bg-surface px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent"
        >
          <Plus className="h-4 w-4" />
          {t("sidebar.new_conversation")}
        </button>
      </div>

      <div className="px-3 pt-3">
        <div className="relative">
          {/* Logical-property positioning so the icon flips to the right edge in RTL. */}
          <Search className="pointer-events-none absolute start-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            placeholder={t("sidebar.search.placeholder")}
            className="h-9 w-full rounded-md border border-transparent bg-surface/60 ps-8 pe-3 text-[13px] text-foreground placeholder:text-muted-foreground focus:border-border focus:outline-none"
          />
        </div>
      </div>

      <div className="mt-5 flex-1 overflow-y-auto px-3 pb-3">
        <div className="px-2 pb-2 text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
          {t("sidebar.recent")}
        </div>
        <ul className="space-y-0.5">
          {conversations.map((c) => {
            const isEditing = editingId === c.id;
            return (
              <li key={c.id}>
                <div
                  className={cn(
                    "group flex w-full items-start gap-2.5 rounded-md px-2.5 py-2 text-left text-[13px] transition-colors",
                    activeId === c.id
                      ? "bg-accent text-accent-foreground"
                      : "text-sidebar-foreground hover:bg-accent/60"
                  )}
                >
                  <MessageSquare className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    {isEditing ? (
                      <input
                        ref={inputRef}
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        onBlur={commit}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            commit();
                          } else if (e.key === "Escape") {
                            e.preventDefault();
                            cancel();
                          }
                        }}
                        maxLength={120}
                        className="w-full rounded border border-border bg-surface px-1.5 py-0.5 text-[13px] font-medium leading-snug text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                      />
                    ) : (
                      <button
                        type="button"
                        onClick={() => onSelect(c.id)}
                        onDoubleClick={() => onRename && beginEdit(c.id, c.title)}
                        className="block w-full truncate text-start font-medium leading-snug"
                        title={onRename ? "Click to open, double-click to rename" : c.title}
                      >
                        {c.title}
                      </button>
                    )}
                    <div className="mt-0.5 flex items-center gap-1 text-[11px] text-muted-foreground">
                      <Clock className="h-3 w-3" />
                      {c.date}
                    </div>
                  </div>
                  {onRename && !isEditing && (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        beginEdit(c.id, c.title);
                      }}
                      className="opacity-0 transition-opacity group-hover:opacity-100 rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
                      aria-label={t("sidebar.rename_conversation")}
                      title={t("sidebar.rename_conversation")}
                    >
                      <Pencil className="h-3 w-3" />
                    </button>
                  )}
                </div>
              </li>
            );
          })}
          {conversations.length === 0 && (
            <li className="px-2 py-6 text-center text-[12px] text-muted-foreground">
              No conversations yet
            </li>
          )}
        </ul>
      </div>

      <div className="border-t border-sidebar-border p-3">
        <div className="flex items-center gap-2 rounded-md bg-surface/60 px-3 py-2.5">
          <Shield className="h-3.5 w-3.5 text-primary" />
          <div className="flex flex-col leading-tight">
            <span className="text-[11px] font-medium text-foreground">Local & Private</span>
            <span className="text-[10px] text-muted-foreground">{t("sidebar.no_data_leaves")}</span>
          </div>
        </div>
        <div className="mt-3 flex items-center gap-2 px-1">
          <div className="flex h-7 w-7 items-center justify-center rounded-full bg-accent text-[11px] font-semibold text-foreground">
            {initialsFor(userName)}
          </div>
          <div className="flex min-w-0 flex-1 flex-col leading-tight">
            <span className="truncate text-[12px] font-medium text-foreground">
              {userName ?? "Signed in"}
            </span>
            <span className="truncate text-[11px] text-muted-foreground">
              {userName === "admin" ? "Administrator" : "LocallyAI user"}
            </span>
          </div>
          <SettingsToggle />
          {onSignOut && (
            <button
              onClick={onSignOut}
              className="rounded p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
              aria-label={t("sidebar.sign_out")}
              title={t("sidebar.sign_out")}
            >
              <LogOut className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>
    </aside>
  );
}
