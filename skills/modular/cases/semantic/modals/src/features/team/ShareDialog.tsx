import { useEffect, useState } from "react";
import { Copy, Check } from "lucide-react";

export function ShareDialog({ open, link, onClose }: { open: boolean; link: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [open, onClose]);

  if (!open) return null;

  async function copy() {
    await navigator.clipboard.writeText(link);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()} className="w-full max-w-lg rounded-lg bg-white p-6 shadow-xl">
        <h2 className="text-lg font-semibold text-gray-900">Share with your team</h2>
        <div className="mt-4 flex items-center gap-2">
          <input readOnly value={link} className="flex-1 rounded-md border px-3 py-2 text-sm text-gray-700" />
          <button onClick={copy} className="rounded-md border p-2" aria-label="Copy link">
            {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
          </button>
        </div>
        <div className="mt-6 flex justify-end">
          <button onClick={onClose} className="rounded-md border px-4 py-2 text-sm">
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
