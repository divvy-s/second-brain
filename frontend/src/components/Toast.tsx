import { createPortal } from "react-dom";
import { Check, X } from "lucide-react";
import { useToast, type Toast as ToastType } from "../context/ToastContext";

function ToastCard({ toast }: { toast: ToastType }) {
  const { dismissToast } = useToast();

  const handleApprove = () => {
    toast.onApprove?.();
    dismissToast(toast.id);
  };
  const handleReject = () => {
    toast.onReject?.();
    dismissToast(toast.id);
  };

  return (
    <div className={`toast-card toast-${toast.type}`}>
      <div className="toast-content">
        <div className="toast-title">{toast.title}</div>
        <div className="toast-body">{toast.body}</div>
      </div>
      <div className="toast-actions">
        {toast.onApprove && (
          <button className="btn btn-icon success" onClick={handleApprove} title="Approve">
            <Check size={14} />
          </button>
        )}
        {toast.onReject && (
          <button className="btn btn-icon danger" onClick={handleReject} title="Reject">
            <X size={14} />
          </button>
        )}
        {!toast.onApprove && !toast.onReject && (
          <button className="btn btn-icon" onClick={() => dismissToast(toast.id)} title="Dismiss">
            <X size={14} />
          </button>
        )}
      </div>
    </div>
  );
}

export function ToastContainer() {
  const { toasts } = useToast();
  if (toasts.length === 0) return null;

  return createPortal(
    <div className="toast-stack">
      {toasts.map((toast) => (
        <ToastCard key={toast.id} toast={toast} />
      ))}
    </div>,
    document.body
  );
}
