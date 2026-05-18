// Page-level drag-and-drop overlay. Wraps the workspace; shows a
// frosted overlay with "Drop documents to ingest" only when the user
// is actively dragging files over the window. Invisible otherwise —
// no permanent visual weight.
//
// Detects drag-enter at window level (not just over a specific zone)
// because users dragging from Finder almost never aim precisely. A
// counter (not a boolean) tracks dragenter/dragleave because every
// child element fires its own dragenter when the cursor moves over
// it; without the counter the overlay flashes off and on.

import { useEffect, useRef, useState } from "react";
import { FileUp } from "lucide-react";
import { uploadFiles } from "@/hooks/use-uploads";
import { t } from "@/lib/i18n";

interface Props {
  children: React.ReactNode;
}

export function UploadDropZone({ children }: Props) {
  const [dragging, setDragging] = useState(false);
  const counter = useRef(0);

  useEffect(() => {
    const onDragEnter = (e: DragEvent) => {
      // Only respond to file drags, not text/element drags inside the app.
      if (!e.dataTransfer || !Array.from(e.dataTransfer.types).includes("Files")) return;
      counter.current += 1;
      setDragging(true);
    };
    const onDragLeave = (e: DragEvent) => {
      if (!e.dataTransfer || !Array.from(e.dataTransfer.types).includes("Files")) return;
      counter.current = Math.max(0, counter.current - 1);
      if (counter.current === 0) setDragging(false);
    };
    const onDragOver = (e: DragEvent) => {
      // Required so the browser allows a drop. Without preventDefault()
      // here, the browser tries to navigate to the file URL.
      if (e.dataTransfer && Array.from(e.dataTransfer.types).includes("Files")) {
        e.preventDefault();
      }
    };
    const onDrop = (e: DragEvent) => {
      if (!e.dataTransfer || !Array.from(e.dataTransfer.types).includes("Files")) return;
      e.preventDefault();
      counter.current = 0;
      setDragging(false);
      if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        void uploadFiles(e.dataTransfer.files);
      }
    };
    window.addEventListener("dragenter", onDragEnter);
    window.addEventListener("dragleave", onDragLeave);
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragenter", onDragEnter);
      window.removeEventListener("dragleave", onDragLeave);
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("drop", onDrop);
    };
  }, []);

  return (
    <>
      {children}
      <div
        className={[
          "pointer-events-none fixed inset-0 z-50 flex items-center justify-center",
          "bg-background/70 backdrop-blur-md transition-opacity duration-150",
          dragging ? "opacity-100" : "opacity-0",
        ].join(" ")}
        aria-hidden={!dragging}
      >
        <div
          className={[
            "flex flex-col items-center gap-4 rounded-2xl border-2 border-dashed border-primary/60",
            "bg-surface/95 px-12 py-10 shadow-2xl",
            "transition-transform duration-150",
            dragging ? "scale-100" : "scale-95",
          ].join(" ")}
        >
          <div className="flex h-16 w-16 items-center justify-center rounded-full bg-primary/15 text-primary">
            <FileUp className="h-8 w-8" />
          </div>
          <div className="text-center">
            <div className="text-[16px] font-semibold text-foreground">
              {t("upload.drop_title", "Drop documents to ingest")}
            </div>
            <div className="mt-1 text-[12px] text-muted-foreground">
              {t("upload.drop_hint", "PDF, DOCX, TXT, MD · max 50 MB each")}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
