"use client";

import { useEffect, useState, useCallback } from "react";
import { X, AlertCircle, CheckCircle, Info } from "lucide-react";

export interface ToastData {
  id: string;
  type: "error" | "success" | "info";
  message: string;
}

let _addToast: ((toast: Omit<ToastData, "id">) => void) | null = null;

/** Global function to show a toast from anywhere (stores, hooks, etc.) */
export function showToast(type: ToastData["type"], message: string) {
  _addToast?.({ type, message });
}

export default function ToastContainer() {
  const [toasts, setToasts] = useState<ToastData[]>([]);

  const addToast = useCallback((toast: Omit<ToastData, "id">) => {
    const id = crypto.randomUUID();
    setToasts((prev) => [...prev, { ...toast, id }]);
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // Register global addToast
  useEffect(() => {
    _addToast = addToast;
    return () => {
      _addToast = null;
    };
  }, [addToast]);

  return (
    <div className="fixed right-4 top-4 z-50 flex flex-col gap-2">
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} onDismiss={removeToast} />
      ))}
    </div>
  );
}

function ToastItem({
  toast,
  onDismiss,
}: {
  toast: ToastData;
  onDismiss: (id: string) => void;
}) {
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(toast.id), 5000);
    return () => clearTimeout(timer);
  }, [toast.id, onDismiss]);

  const icons = {
    error: <AlertCircle size={16} className="text-danger" />,
    success: <CheckCircle size={16} className="text-success" />,
    info: <Info size={16} className="text-accent" />,
  };

  const borders = {
    error: "border-danger/30",
    success: "border-success/30",
    info: "border-accent/30",
  };

  return (
    <div
      className={`flex items-start gap-2 rounded border ${borders[toast.type]} bg-bg-secondary px-3 py-2 shadow-lg animate-in fade-in slide-in-from-right-2`}
      style={{ maxWidth: "360px" }}
    >
      <span className="mt-0.5 shrink-0">{icons[toast.type]}</span>
      <p className="flex-1 text-xs text-text-primary">{toast.message}</p>
      <button
        onClick={() => onDismiss(toast.id)}
        className="shrink-0 text-text-secondary hover:text-text-primary"
      >
        <X size={14} />
      </button>
    </div>
  );
}
