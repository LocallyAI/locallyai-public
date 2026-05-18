import { Paperclip, ArrowUp, FileText, FolderUp, ChevronDown } from "lucide-react";
import { useRef, useState } from "react";
import { t } from "@/lib/i18n";
import { uploadFiles } from "@/hooks/use-uploads";
import { toast } from "sonner";
import { RecentDocuments } from "./RecentDocuments";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

interface Props {
  onSend: (text: string) => void;
  disabled?: boolean;
}

// Non-standard but universally-supported attribute on <input type=file>.
// React's TS def doesn't know about it, so we cast at the JSX site.
type DirInputProps = React.InputHTMLAttributes<HTMLInputElement> & {
  webkitdirectory?: string; directory?: string;
};

export function Composer({ onSend, disabled }: Props) {
  const [value, setValue] = useState("");
  const fileInput   = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const [attachOpen, setAttachOpen] = useState(false);

  const submit = () => {
    if (!value.trim() || disabled) return;
    onSend(value.trim());
    setValue("");
  };

  const ALLOWED_EXTS = new Set(["pdf", "docx", "txt", "md"]);
  const onPickFiles = (e: React.ChangeEvent<HTMLInputElement>) => {
    const all = e.target.files ? Array.from(e.target.files) : [];
    if (all.length > 0) {
      // For folder picks (where the OS hands us hundreds of unrelated
      // files), silently keep only what we can ingest and surface a single
      // summary toast. Single-file picks fall through to per-file
      // validation in use-uploads (which toasts the rejection reason).
      const isFolderPick = all.length > 1 && all.some((f) => /[\\/]/.test((f as File & { webkitRelativePath?: string }).webkitRelativePath ?? ""));
      const accepted = isFolderPick
        ? all.filter((f) => ALLOWED_EXTS.has(f.name.split(".").pop()?.toLowerCase() ?? ""))
        : all;
      const skipped = all.length - accepted.length;
      if (accepted.length > 0) void uploadFiles(accepted);
      if (isFolderPick && skipped > 0) {
        toast.info(`Skipped ${skipped} unsupported file${skipped === 1 ? "" : "s"} in folder.`);
      }
      if (isFolderPick && accepted.length === 0) {
        toast.error("No supported files found in folder.");
      }
    }
    e.target.value = "";
    setAttachOpen(false);
  };

  return (
    <div className="mx-auto w-full max-w-3xl px-6 pb-6">
      <div className="rounded-2xl border border-border bg-surface shadow-[0_1px_0_0_rgba(255,255,255,0.02)_inset,0_8px_32px_-12px_rgba(0,0,0,0.4)] transition-colors focus-within:border-primary/50">
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          rows={2}
          placeholder={t("composer.placeholder")}
          className="block w-full resize-none rounded-t-2xl bg-transparent px-5 pt-4 pb-2 text-[14px] leading-relaxed text-foreground placeholder:text-muted-foreground focus:outline-none"
        />
        <div className="flex items-center justify-between gap-2 px-3 pb-3 pt-1">
          <div className="flex items-center gap-1">
            <input
              ref={fileInput}
              type="file"
              multiple
              accept=".pdf,.docx,.txt,.md"
              onChange={onPickFiles}
              className="hidden"
              aria-hidden
            />
            {/* Folder picker — `webkitdirectory` is supported in every
                evergreen browser; the file list arrives flat. */}
            <input
              {...({
                ref: folderInput,
                type: "file",
                multiple: true,
                webkitdirectory: "",
                directory: "",
                onChange: onPickFiles,
                className: "hidden",
                "aria-hidden": true,
              } as DirInputProps)}
            />
            <Popover open={attachOpen} onOpenChange={setAttachOpen}>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                >
                  <Paperclip className="h-3.5 w-3.5" />
                  {t("composer.attach")}
                  <ChevronDown className="h-3 w-3 opacity-60" />
                </button>
              </PopoverTrigger>
              <PopoverContent align="start" sideOffset={8} className="w-52 p-1">
                <button
                  type="button"
                  onClick={() => fileInput.current?.click()}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-[12.5px] hover:bg-accent"
                >
                  <Paperclip className="h-3.5 w-3.5 text-muted-foreground" />
                  {t("composer.pick_files", "Pick files")}
                </button>
                <button
                  type="button"
                  onClick={() => folderInput.current?.click()}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-[12.5px] hover:bg-accent"
                >
                  <FolderUp className="h-3.5 w-3.5 text-muted-foreground" />
                  {t("composer.pick_folder", "Pick folder")}
                </button>
              </PopoverContent>
            </Popover>
            <RecentDocuments
              trigger={
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                >
                  <FileText className="h-3.5 w-3.5" />
                  {t("composer.recent_documents")}
                </button>
              }
            />
          </div>
          <button
            onClick={submit}
            disabled={!value.trim() || disabled}
            className="flex h-8 w-8 items-center justify-center rounded-md bg-foreground text-background transition-all hover:opacity-90 active:scale-95 disabled:opacity-30 disabled:active:scale-100"
            aria-label={t("composer.send")}
          >
            {/* The arrow flips with dir via CSS rtl:rotate-180 — Tailwind v4 ships the rtl: variant. */}
            <ArrowUp className="h-4 w-4 rtl:rotate-180" />
          </button>
        </div>
      </div>
      <div className="mt-3 text-center text-[11px] text-muted-foreground">
        {t("composer.disclaimer")}
      </div>
    </div>
  );
}
