// Tiny per-extension icon component. Replaces the generic FileText icon
// in the composer chip, the recent-documents popover, and the sources
// panel. Uses lucide's existing variants where they exist; falls back
// to FileText.

import { FileText, FileType2, FileImage, FileCode, File as FileGeneric } from "lucide-react";

interface Props {
  ext: string;
  className?: string;
}

export function FileTypeIcon({ ext, className }: Props) {
  const e = ext.toLowerCase().replace(/^\./, "");
  // We currently support pdf, docx, txt, md per /v1/ingest's _ALLOWED_EXTS.
  // The other variants are forward-looking — drop in when ingest grows.
  switch (e) {
    case "pdf":
      // Distinct red-ish tint at the consumer's discretion via className.
      return <FileType2 className={className} />;
    case "docx":
    case "doc":
      return <FileText className={className} />;
    case "md":
    case "markdown":
      return <FileCode className={className} />;
    case "txt":
      return <FileText className={className} />;
    case "png":
    case "jpg":
    case "jpeg":
    case "webp":
      return <FileImage className={className} />;
    default:
      return <FileGeneric className={className} />;
  }
}
