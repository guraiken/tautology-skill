import { useEffect, useRef, useState } from "react";

interface RenameFileDialogProps {
  open: boolean;
  initialName: string;
  onClose: () => void;
  onRename: (name: string) => Promise<void>;
}

export function RenameFileDialog({ open, initialName, onClose, onRename }: RenameFileDialogProps) {
  const [name, setName] = useState(initialName);
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setName(initialName);
    inputRef.current?.select();
    function handleKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [open, initialName, onClose]);

  if (!open) return null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    await onRename(name.trim());
    setSaving(false);
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onMouseDown={onClose}>
      <form
        role="dialog"
        aria-modal="true"
        onSubmit={submit}
        onMouseDown={(e) => e.stopPropagation()}
        className="bg-white rounded-lg shadow-xl p-6 w-full max-w-md"
      >
        <h2 className="text-lg font-semibold text-gray-900">Rename file</h2>
        <label className="mt-4 block text-sm font-medium text-gray-700">
          New name
          <input
            ref={inputRef}
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mt-1 w-full rounded-md border px-3 py-2 text-sm"
          />
        </label>
        <div className="mt-6 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-md border px-4 py-2 text-sm">
            Cancel
          </button>
          <button type="submit" disabled={saving} className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white">
            {saving ? "Saving..." : "Rename"}
          </button>
        </div>
      </form>
    </div>
  );
}
