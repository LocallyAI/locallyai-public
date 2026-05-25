import { useEffect, useState } from "react";
import { ChevronDown, X, Puzzle, AlertTriangle } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  listPlugins,
  type PluginSummary,
  type ToolCallingCapability,
} from "@/lib/api";

interface Props {
  selectedPlugin: string | null;
  selectedSkill: string | null;
  onPluginChange: (plugin: string | null) => void;
  onSkillChange: (skill: string | null) => void;
  /** Capability of the *currently active* model. When "fails", the
   *  picker is hard-disabled — picking a plugin would have no effect
   *  because the model can't issue tool calls. */
  toolCalling: ToolCallingCapability | undefined;
}

type LoadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; plugins: PluginSummary[] }
  | { kind: "error"; message: string };

/** Picker shown above the chat composer. Two compact dropdowns:
 *  Plugin (e.g. "ip-legal") and Skill (e.g. "clearance"). Selections
 *  are forwarded up so the parent can splice them into the chat
 *  request payload. Refetches plugins each time the Plugin dropdown
 *  opens — the Manager UI can toggle plugins independently, and a
 *  stale list would confuse demos. */
export function PluginPicker({
  selectedPlugin,
  selectedSkill,
  onPluginChange,
  onSkillChange,
  toolCalling,
}: Props) {
  const [pluginOpen, setPluginOpen] = useState(false);
  const [skillOpen, setSkillOpen] = useState(false);
  const [state, setState] = useState<LoadState>({ kind: "idle" });

  const fetchPlugins = async () => {
    setState({ kind: "loading" });
    try {
      const plugins = await listPlugins();
      setState({ kind: "ready", plugins });
    } catch (err) {
      const message =
        err instanceof Error
          ? err.message
          : "Failed to load plugins (offline?)";
      setState({ kind: "error", message });
    }
  };

  // Refetch every time the user opens the Plugin dropdown — the Manager
  // UI can have enabled/disabled plugins between sends, and showing the
  // stale list confuses live demos. Cheap call.
  useEffect(() => {
    if (pluginOpen) void fetchPlugins();
  }, [pluginOpen]);

  // The hard-disable case: the active model cannot call tools at all.
  const failsTools = toolCalling === "fails";
  // Treat missing capability as "unverified" — older backends omit the
  // field, and we'd rather warn than silently allow.
  const unverified = toolCalling === "unverified" || toolCalling === undefined;

  const plugins = state.kind === "ready" ? state.plugins : [];
  const activePlugin = plugins.find((p) => p.name === selectedPlugin) ?? null;
  const skills = activePlugin?.skills ?? [];
  const activeSkill = skills.find((s) => s.name === selectedSkill) ?? null;

  const clearAll = () => {
    onPluginChange(null);
    onSkillChange(null);
  };

  const pickPlugin = (name: string | null) => {
    onPluginChange(name);
    // Always reset the skill — old selection won't exist in the new plugin.
    onSkillChange(null);
    setPluginOpen(false);
  };

  const pickSkill = (name: string | null) => {
    onSkillChange(name);
    setSkillOpen(false);
  };

  // Compact chip styling matches the header pills (rounded-full, border,
  // bg-surface/60) so the picker reads as ambient chrome rather than a
  // primary control.
  const chipBase =
    "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border border-border bg-surface/60 px-2.5 py-1 text-[11.5px] text-foreground transition-colors hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50";

  return (
    <div className="mx-auto w-full max-w-3xl px-6 pt-2">
      <div className="flex flex-wrap items-center gap-2">
        {/* Plugin dropdown */}
        <Popover open={pluginOpen} onOpenChange={setPluginOpen}>
          <PopoverTrigger asChild>
            <button
              type="button"
              disabled={failsTools}
              title={
                failsTools
                  ? "Switch to a curated model to use plugins"
                  : "Pick a plugin to enrich this conversation with a practice profile + MCP tools"
              }
              className={chipBase}
            >
              <Puzzle className="h-3 w-3 text-muted-foreground" />
              <span className="text-muted-foreground">Plugin:</span>
              <span className="max-w-[18ch] truncate font-medium">
                {selectedPlugin ?? "(none)"}
              </span>
              <ChevronDown className="h-3 w-3 opacity-60" />
            </button>
          </PopoverTrigger>
          <PopoverContent
            align="start"
            sideOffset={6}
            className="w-80 max-h-[60vh] overflow-y-auto p-1"
          >
            <button
              type="button"
              onClick={() => pickPlugin(null)}
              className="flex w-full items-start gap-2 rounded px-2 py-1.5 text-left hover:bg-accent"
            >
              <span className="text-[12.5px] font-medium text-muted-foreground">
                (none)
              </span>
            </button>
            {state.kind === "loading" && (
              <div className="px-2 py-3 text-center text-[12px] text-muted-foreground">
                Loading plugins…
              </div>
            )}
            {state.kind === "error" && (
              <div className="px-2 py-3 text-center text-[12px] text-destructive">
                {state.message || "Failed to load plugins (offline?)"}
              </div>
            )}
            {state.kind === "ready" && plugins.length === 0 && (
              <div className="px-2 py-3 text-center text-[12px] text-muted-foreground">
                No plugins installed. Ask your admin to install via
                Manager UI &rarr; Plugins tab.
              </div>
            )}
            {state.kind === "ready" &&
              plugins.map((p) => (
                <button
                  key={p.name}
                  type="button"
                  onClick={() => pickPlugin(p.name)}
                  className={
                    "flex w-full flex-col items-start gap-0.5 rounded px-2 py-1.5 text-left hover:bg-accent " +
                    (p.name === selectedPlugin ? "bg-accent/60" : "")
                  }
                >
                  <span className="text-[12.5px] font-semibold text-foreground">
                    {p.name}
                  </span>
                  <span className="line-clamp-2 text-[11px] text-muted-foreground">
                    {p.description}
                  </span>
                </button>
              ))}
          </PopoverContent>
        </Popover>

        {/* Skill dropdown — disabled until a plugin is picked */}
        <Popover open={skillOpen} onOpenChange={setSkillOpen}>
          <PopoverTrigger asChild>
            <button
              type="button"
              disabled={!selectedPlugin || failsTools}
              title={
                !selectedPlugin
                  ? "Pick a plugin first"
                  : "Pick a skill within the active plugin"
              }
              className={chipBase}
            >
              <span className="text-muted-foreground">Skill:</span>
              <span className="max-w-[18ch] truncate font-medium">
                {selectedSkill ?? "(none)"}
              </span>
              <ChevronDown className="h-3 w-3 opacity-60" />
            </button>
          </PopoverTrigger>
          <PopoverContent
            align="start"
            sideOffset={6}
            className="w-80 max-h-[60vh] overflow-y-auto p-1"
          >
            <button
              type="button"
              onClick={() => pickSkill(null)}
              className="flex w-full items-start gap-2 rounded px-2 py-1.5 text-left hover:bg-accent"
            >
              <span className="text-[12.5px] font-medium text-muted-foreground">
                (none)
              </span>
            </button>
            {skills.length === 0 && (
              <div className="px-2 py-3 text-center text-[12px] text-muted-foreground">
                This plugin has no skills.
              </div>
            )}
            {skills.map((s) => (
              <button
                key={s.name}
                type="button"
                onClick={() => pickSkill(s.name)}
                className={
                  "flex w-full flex-col items-start gap-0.5 rounded px-2 py-1.5 text-left hover:bg-accent " +
                  (s.name === selectedSkill ? "bg-accent/60" : "")
                }
              >
                <span className="text-[12.5px] font-semibold text-foreground">
                  {s.name}
                </span>
                <span className="line-clamp-2 text-[11px] text-muted-foreground">
                  {s.description}
                </span>
              </button>
            ))}
          </PopoverContent>
        </Popover>

        {/* Active combo pill + clear */}
        {selectedPlugin && (
          <span className="inline-flex items-center gap-1 rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
            {selectedPlugin}
            {activeSkill ? ` · ${activeSkill.name}` : ""}
            <button
              type="button"
              onClick={clearAll}
              aria-label="Clear plugin selection"
              className="ms-1 rounded-full p-0.5 hover:bg-primary/20"
            >
              <X className="h-2.5 w-2.5" />
            </button>
          </span>
        )}

        {/* Capability warnings */}
        {failsTools && (
          <span
            className="inline-flex items-center gap-1 rounded-full border border-destructive/40 bg-destructive/10 px-2 py-0.5 text-[10.5px] font-medium text-destructive"
            title="The selected model failed tool-calling verification. Switch to a curated model to use plugins."
          >
            <AlertTriangle className="h-2.5 w-2.5" />
            tools unavailable
          </span>
        )}
        {!failsTools && unverified && (
          <span
            className="inline-flex items-center gap-1 rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[10.5px] font-medium text-amber-600 dark:text-amber-300"
            title="This model has not been verified for tool calling. Plugins may behave unpredictably."
          >
            <AlertTriangle className="h-2.5 w-2.5" />
            unverified for tools
          </span>
        )}
      </div>
    </div>
  );
}
