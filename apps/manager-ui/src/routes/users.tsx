import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { TopBar } from "@/components/TopBar";
import { Plus, KeyRound, Copy, Trash2, AlertTriangle, Check, RotateCw } from "lucide-react";
import { listUsers, createUser, deleteUser, rotateUserKey, type UserKeyResponse } from "@/lib/api";

export const Route = createFileRoute("/users")({
  head: () => ({ meta: [{ title: "Users — LocallyAI" }] }),
  component: UsersPage,
});

function UsersPage() {
  const [users, setUsers] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [recentlyMinted, setRecentlyMinted] = useState<UserKeyResponse | null>(null);
  const [copyOk, setCopyOk] = useState(false);

  const refresh = async () => {
    try {
      const list = await listUsers();
      setUsers(list);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load users");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newName.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const minted = await createUser(newName.trim());
      setRecentlyMinted(minted);
      setNewName("");
      setShowAdd(false);
      await refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to add user");
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (name: string) => {
    if (!window.confirm(`Remove user "${name}"? Their API key will stop working immediately.`)) return;
    setBusy(true);
    setError(null);
    try {
      await deleteUser(name);
      await refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to remove user");
    } finally {
      setBusy(false);
    }
  };

  const handleRotate = async (name: string) => {
    if (!window.confirm(`Rotate API key for "${name}"? The previous key will stop working immediately.`)) return;
    setBusy(true);
    setError(null);
    try {
      const minted = await rotateUserKey(name);
      setRecentlyMinted(minted);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to rotate key");
    } finally {
      setBusy(false);
    }
  };

  const copyKey = async (key: string) => {
    try {
      await navigator.clipboard.writeText(key);
      setCopyOk(true);
      window.setTimeout(() => setCopyOk(false), 1500);
    } catch {
      // clipboard may be blocked; the user can still select & copy manually
    }
  };

  return (
    <>
      <TopBar title="User Management" description="Provision, rotate, and revoke API keys" />
      <main className="flex-1 space-y-6 p-6">
        {error && (
          <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {recentlyMinted && (
          <div className="rounded-lg border border-warning/40 bg-warning/5 p-4">
            <div className="flex items-start gap-3">
              <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
              <div className="flex-1 min-w-0">
                <div className="text-sm font-semibold">
                  API key for {recentlyMinted.name}
                </div>
                <div className="mt-1 text-xs text-muted-foreground">{recentlyMinted.warning}</div>
                <div className="mt-3 flex items-center gap-2 rounded-md border border-border bg-background p-2">
                  <code className="terminal-font flex-1 break-all text-xs">{recentlyMinted.api_key}</code>
                  <button
                    onClick={() => copyKey(recentlyMinted.api_key)}
                    className="flex items-center gap-1 rounded-md border border-border bg-secondary px-2 py-1 text-xs hover:bg-accent"
                  >
                    {copyOk ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                    {copyOk ? "Copied" : "Copy"}
                  </button>
                </div>
                <button
                  onClick={() => setRecentlyMinted(null)}
                  className="mt-3 text-xs text-muted-foreground hover:text-foreground"
                >
                  Dismiss
                </button>
              </div>
            </div>
          </div>
        )}

        <div className="rounded-lg border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border p-4">
            <div>
              <h2 className="text-sm font-semibold">Users</h2>
              <p className="text-xs text-muted-foreground">
                {loading ? "Loading…" : `${users.length} user${users.length === 1 ? "" : "s"}`}
              </p>
            </div>
            <button
              onClick={() => setShowAdd((s) => !s)}
              className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90"
            >
              <Plus className="h-3.5 w-3.5" />Add user
            </button>
          </div>

          {showAdd && (
            <form onSubmit={handleAdd} className="border-b border-border bg-secondary/30 p-4">
              <label className="block text-xs font-medium">Display name</label>
              <div className="mt-1 flex gap-2">
                <input
                  autoFocus
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="e.g. Sarah Chen"
                  className="h-9 flex-1 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
                />
                <button
                  type="submit"
                  disabled={busy || !newName.trim()}
                  className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-40"
                >
                  Create & generate key
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setShowAdd(false);
                    setNewName("");
                  }}
                  className="rounded-md border border-border bg-secondary px-3 py-1.5 text-xs"
                >
                  Cancel
                </button>
              </div>
              <p className="mt-2 text-[11px] text-muted-foreground">
                A 64-character hex key will be generated and shown once. Hand it to the user securely.
              </p>
            </form>
          )}

          {!loading && users.length === 0 ? (
            <div className="px-4 py-10 text-center text-sm text-muted-foreground">
              No users yet. Add one to get started.
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase tracking-wider text-muted-foreground">
                  <th className="px-4 py-2 text-start font-medium">User</th>
                  <th className="px-4 py-2 text-end font-medium"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {users.map((name) => (
                  <tr key={name} className="hover:bg-accent/30">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-3">
                        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-secondary text-xs font-medium uppercase">
                          {name
                            .split(/\s+/)
                            .slice(0, 2)
                            .map((p) => p.charAt(0))
                            .join("")
                            .toUpperCase() || name.slice(0, 2).toUpperCase()}
                        </div>
                        <div>
                          <div className="text-sm">{name}</div>
                          <div className="terminal-font text-xs text-muted-foreground">
                            User · API key issued
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-end">
                      <div className="inline-flex gap-2">
                        <button
                          onClick={() => handleRotate(name)}
                          disabled={busy}
                          className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs hover:bg-accent disabled:opacity-40"
                        >
                          <RotateCw className="h-3 w-3" />
                          Rotate
                        </button>
                        <button
                          onClick={() => handleDelete(name)}
                          disabled={busy}
                          className="flex items-center gap-1 rounded-md border border-destructive/40 bg-destructive/10 px-2 py-1 text-xs text-destructive hover:bg-destructive/20 disabled:opacity-40"
                        >
                          <Trash2 className="h-3 w-3" />
                          Remove
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </main>
    </>
  );
}
