import { useEffect, useRef, useCallback } from "react";

/**
 * Hook that requests browser notification permission and provides
 * a notify() function to trigger native OS push notifications.
 */
export function useNotifications() {
  const permissionRef = useRef<NotificationPermission>("default");

  useEffect(() => {
    if ("Notification" in window) {
      Notification.requestPermission().then((perm) => {
        permissionRef.current = perm;
      });
    }
  }, []);

  const notify = useCallback(
    (title: string, body: string, onClick?: () => void) => {
      if (permissionRef.current !== "granted") return;
      try {
        const n = new Notification(title, {
          body,
          icon: "/icon.png",
          tag: `sb-${Date.now()}`,
        });
        if (onClick) {
          n.onclick = () => {
            window.focus();
            onClick();
          };
        }
      } catch {
        // Notification constructor can fail in some environments
      }
    },
    []
  );

  return { notify };
}
