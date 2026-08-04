"use client";

import { useEffect, useRef, useState } from "react";

// Name prompt for the first save of a working deck. Subsequent saves update
// the existing SavedDeck without reopening this dialog.
export function SaveDeckDialog({
  open,
  defaultName,
  onSave,
  onClose,
}: {
  open: boolean;
  defaultName: string;
  onSave: (name: string) => void;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [name, setName] = useState(defaultName);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      setName(defaultName);
      dialog.showModal();
    }
    if (!open && dialog.open) dialog.close();
  }, [open, defaultName]);

  const submit = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    onSave(trimmed);
    dialogRef.current?.close();
  };

  return (
    <dialog
      ref={dialogRef}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === dialogRef.current) dialogRef.current?.close();
      }}
      className="w-full max-w-sm rounded-2xl border border-line bg-panel p-0 text-ink shadow-2xl backdrop:bg-transparent"
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        className="p-6"
      >
        <h2 className="text-lg font-semibold">Save deck</h2>
        <label className="mt-4 block text-sm text-ink-muted">
          Deck name
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
            maxLength={80}
            className="mt-1.5 h-10 w-full rounded-lg border border-line bg-bg px-3 text-sm text-ink placeholder:text-ink-muted/70 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
          />
        </label>
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => dialogRef.current?.close()}
            className="h-10 cursor-pointer rounded-lg border border-line bg-panel px-4 text-sm text-ink-muted transition-colors duration-150 hover:border-ink-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!name.trim()}
            className="h-10 cursor-pointer rounded-lg bg-action px-4 text-sm font-medium text-white transition-colors duration-150 hover:bg-action/85 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
          >
            Save
          </button>
        </div>
      </form>
    </dialog>
  );
}
