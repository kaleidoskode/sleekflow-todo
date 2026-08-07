import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

interface ModalProps {
  /** Announced as the dialog's accessible name. */
  label: string;
  onClose: () => void;
  children: ReactNode;
}

/**
 * Wraps the native `<dialog>` element rather than rebuilding one.
 * `showModal()` gives focus trapping, the inert backdrop, Escape-to-close
 * and focus restoration for free — all things a hand-rolled overlay
 * usually gets wrong.
 */
export function Modal({ label, onClose, children }: ModalProps) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
    return () => dialog?.close();
  }, []);

  return (
    <dialog
      ref={ref}
      className="modal"
      aria-label={label}
      // Escape fires `cancel`; route it through the same close path so the
      // parent's state stays in sync with the DOM.
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      // A click landing on the dialog itself (not its content) is a
      // backdrop click, since the content is wrapped below.
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
    >
      <div className="modal-inner">{children}</div>
    </dialog>
  );
}
