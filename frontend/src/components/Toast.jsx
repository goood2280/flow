import { useEffect, useState } from "react";
import { statusPalette } from "./UXKit";

let items = [];
let listeners = [];
let seq = 0;

function emit() {
  for (const listener of listeners) {
    try {
      listener(items);
    } catch (_) {}
  }
}

function dismiss(id) {
  if (id == null) {
    items = [];
  } else {
    items = items.filter((item) => item.id !== id);
  }
  emit();
}

function push(message, tone = "info", ms = 3500) {
  if (message == null) return null;
  const text = typeof message === "string" ? message : String(message);
  if (!text.trim()) return null;
  const id = ++seq;
  items = [...items, { id, message: text, tone, ms }];
  emit();
  window.setTimeout(() => dismiss(id), ms);
  return id;
}

export const toast = (message, tone = "info", ms) => push(message, tone, ms);
toast.info = (message, ms) => push(message, "info", ms);
toast.ok = (message, ms) => push(message, "ok", ms);
toast.warn = (message, ms) => push(message, "warn", ms || 4500);
toast.error = (message, ms) => push(message, "bad", ms || 5500);
toast.bad = toast.error;
toast.dismiss = dismiss;

export function ToastHost() {
  const [visible, setVisible] = useState(items);

  useEffect(() => {
    const listener = (next) => setVisible([...next]);
    listeners.push(listener);
    return () => {
      listeners = listeners.filter((x) => x !== listener);
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.__flowNativeAlert) return;
    window.__flowNativeAlert = window.alert;
    window.alert = (message) => {
      toast.info(message);
    };
  }, []);

  if (!visible.length) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        position: "fixed",
        top: 64,
        right: 16,
        zIndex: 10050,
        display: "flex",
        flexDirection: "column",
        gap: 8,
        pointerEvents: "none",
        maxWidth: "min(420px, calc(100vw - 32px))",
      }}
    >
      {visible.map((item) => {
        const tone = statusPalette[item.tone] || statusPalette.info;
        const icon = item.tone === "ok" ? "✓" : item.tone === "warn" ? "!" : item.tone === "bad" ? "✕" : "ⓘ";
        return (
          <div
            key={item.id}
            onClick={() => dismiss(item.id)}
            title="클릭하여 닫기"
            style={{
              pointerEvents: "auto",
              cursor: "pointer",
              padding: "10px 14px",
              borderRadius: 6,
              background: "var(--bg-secondary)",
              border: `1px solid ${tone.fg}`,
              borderLeft: `4px solid ${tone.fg}`,
              boxShadow: "0 6px 18px rgba(0,0,0,0.25)",
              color: "var(--text-primary)",
              fontSize: 14,
              lineHeight: 1.4,
              display: "flex",
              alignItems: "flex-start",
              gap: 10,
              animation: "flow-toast-in 0.15s ease-out",
            }}
          >
            <span style={{ color: tone.fg, fontWeight: 700, flexShrink: 0 }}>{icon}</span>
            <span style={{ flex: 1, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{item.message}</span>
          </div>
        );
      })}
      <style>{`
        @keyframes flow-toast-in {
          from { opacity: 0; transform: translateY(-6px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}

export default ToastHost;
