import { toast } from "sonner";

/** Text types a download may be, by extension; anything else downloads as plain text. */
const MIME: Record<string, string> = {
  csv: "text/csv",
  tsv: "text/tab-separated-values",
  json: "application/json",
  md: "text/markdown",
  txt: "text/plain",
};

/**
 * Saves `text` as a file of that name, made in the page: nothing is fetched or uploaded. Used by a
 * named code block (code-block.tsx) and a cheat sheet's Download .md (cheatsheet.tsx). A browser that
 * refuses (no Blob URLs, a blocked download) gets the app's error toast, never a silent no-op.
 */
export function saveText(filename: string, text: string): void {
  try {
    const extension = filename.split(".").pop()?.toLowerCase() ?? "";
    const blob = new Blob([text], { type: `${MIME[extension] ?? "text/plain"};charset=utf-8` });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch {
    toast.error("Couldn't download the file.");
  }
}
