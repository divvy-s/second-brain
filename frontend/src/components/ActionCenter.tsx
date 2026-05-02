import { useState, useEffect, useCallback } from "react";
import { Check, X } from "lucide-react";

type ApprovalRequest = {
  id: string;
  status: string;
  action: {
    type: string;
    plugin: string;
    title?: string;
    description?: string;
    [key: string]: any;
  };
  created_at: string;
};

type ActionCenterProps = {
  isOpen: boolean;
  onClose: () => void;
  approvals: ApprovalRequest[];
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
};

export function ActionCenter({ isOpen, onClose, approvals, onApprove, onReject }: ActionCenterProps) {
  const [currentIndex, setCurrentIndex] = useState(0);

  // Reset index when opened
  useEffect(() => {
    if (isOpen) {
      setCurrentIndex(0);
    }
  }, [isOpen, approvals.length]);

  const current = approvals[currentIndex];

  const handleApprove = useCallback(() => {
    if (!current) return;
    onApprove(current.id);
    // Stay at same index since the current item will be removed from approvals array
  }, [current, onApprove]);

  const handleReject = useCallback(() => {
    if (!current) return;
    onReject(current.id);
  }, [current, onReject]);

  // Keyboard navigation
  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (!current) return;

      if (e.key === "ArrowRight" || e.key.toLowerCase() === "l") {
        e.preventDefault();
        handleApprove();
      } else if (e.key === "ArrowLeft" || e.key.toLowerCase() === "h") {
        e.preventDefault();
        handleReject();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, current, handleApprove, handleReject, onClose]);

  if (!isOpen) return null;

  return (
    <div className="action-center-overlay" onClick={onClose}>
      <div className="action-center-modal" onClick={(e) => e.stopPropagation()}>
        <div className="action-center-header">
          <h2>Action Center</h2>
          <button className="btn btn-icon" onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        {approvals.length === 0 ? (
          <div className="action-center-empty">
            <p>You're all caught up!</p>
            <button className="btn primary" onClick={onClose}>Close</button>
          </div>
        ) : (
          <div className="action-center-content">
            <div className="action-center-progress">
              {currentIndex + 1} of {approvals.length} remaining
            </div>

            {current && (
              <div className="action-card">
                <div className="action-card-header">
                  <span className="action-type">{current.action.type.replace(/_/g, " ")}</span>
                  <span className="action-plugin">{current.action.plugin}</span>
                </div>
                <h3 className="action-title">{current.action.title || "Pending Action"}</h3>
                {current.action.description && (
                  <p className="action-desc">{current.action.description}</p>
                )}
                <div className="action-details">
                  {Object.entries(current.action)
                    .filter(([k]) => !["type", "plugin", "title", "description", "source_event_id", "risk", "effective_score", "priority_score", "recommendation_type"].includes(k))
                    .map(([k, v]) => (
                      <div key={k} className="detail-row">
                        <span className="detail-key">{k}:</span>
                        <span className="detail-value">{String(v)}</span>
                      </div>
                    ))}
                </div>

                <div className="action-controls">
                  <button className="btn danger large" onClick={handleReject}>
                    <X size={18} style={{ marginRight: 8 }} />
                    Reject <kbd>←</kbd>
                  </button>
                  <button className="btn success large" onClick={handleApprove}>
                    <Check size={18} style={{ marginRight: 8 }} />
                    Approve <kbd>→</kbd>
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
