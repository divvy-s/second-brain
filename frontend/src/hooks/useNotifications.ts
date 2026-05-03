import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Hook that requests browser notification permission and provides
 * a notify() function to trigger native OS push notifications.
 */
export function useNotifications() {
  const permissionRef = useRef<NotificationPermission>("default");
  const [permission, setPermission] = useState<NotificationPermission>(() => {
    if (!("Notification" in window)) return "denied";
    return Notification.permission;
  });

  useEffect(() => {
    if ("Notification" in window) {
      permissionRef.current = Notification.permission;
      setPermission(Notification.permission);
    }
  }, []);

  const requestPermission = useCallback(async () => {
    if (!("Notification" in window)) {
      permissionRef.current = "denied";
      setPermission("denied");
      return "denied" as NotificationPermission;
    }
    const next = await Notification.requestPermission();
    permissionRef.current = next;
    setPermission(next);
    return next;
  }, []);

  const notify = useCallback(
    (title: string, body: string, onClick?: () => void) => {
      if (permissionRef.current !== "granted") return;
      try {
        const n = new Notification(title, {
          body,
          icon: "/icon.svg",
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

  return { notify, permission, requestPermission };
}
