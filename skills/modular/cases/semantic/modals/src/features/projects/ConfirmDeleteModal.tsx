import { useEffect, useRef } from "react";

type Props = { open: boolean; projectName: string; onClose: () => void; onConfirm: () => void };

export function ConfirmDeleteModal({ open, projectName, onClose, onConfirm }: Props) {
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    cancelRef.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold text-gray-900">Delete project</h2>
        <p className="mt-2 text-sm text-gray-600">
          This will permanently delete <strong>{projectName}</strong>. This action cannot be undone.
        </p>
        <div className="mt-6 flex justify-end gap-2">
          <button ref={cancelRef} onClick={onClose} className="rounded-md border px-4 py-2 text-sm">
            Cancel
          </button>
          <button onClick={onConfirm} className="rounded-md bg-red-600 px-4 py-2 text-sm text-white hover:bg-red-700">
            Delete
          </button>
        </div>
      </div>
    </div>
  );
}
