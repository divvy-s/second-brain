import { createContext, useContext, useState, useCallback, type ReactNode } from "react";

export type Toast = {
  id: string;
  title: string;
  body: string;
  type: "info" | "warning" | "urgent";
  onApprove?: () => void;
  onReject?: () => void;
  createdAt: number;
};

type ToastContextValue = {
  toasts: Toast[];
  pushToast: (toast: Omit<Toast, "id" | "createdAt">) => void;
  dismissToast: (id: string) => void;
};

const ToastContext = createContext<ToastContextValue>({
  toasts: [],
  pushToast: () => {},
  dismissToast: () => {},
});

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const pushToast = useCallback(
    (toast: Omit<Toast, "id" | "createdAt">) => {
      const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
      const newToast: Toast = { ...toast, id, createdAt: Date.now() };
      setToasts((prev) => [...prev, newToast]);
      // Auto-dismiss after 8 seconds unless user interacts
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, 8000);
    },
    []
  );

  return (
    <ToastContext.Provider value={{ toasts, pushToast, dismissToast }}>
      {children}
    </ToastContext.Provider>
  );
}
