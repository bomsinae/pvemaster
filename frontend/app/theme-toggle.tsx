"use client";

import { useSyncExternalStore } from "react";

type Theme = "dark" | "light";

const storageKey = "pvemaster-theme";
const changeEvent = "pvemaster-theme-change";

function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  try {
    localStorage.setItem(storageKey, theme);
  } catch {
    // The active page still changes theme when browser storage is unavailable.
  }
  window.dispatchEvent(new Event(changeEvent));
}

function subscribe(onStoreChange: () => void) {
  window.addEventListener(changeEvent, onStoreChange);
  return () => window.removeEventListener(changeEvent, onStoreChange);
}

function getTheme(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, getTheme, () => "dark");

  function toggleTheme() {
    const nextTheme = theme === "dark" ? "light" : "dark";
    applyTheme(nextTheme);
  }

  const targetLabel = theme === "dark" ? "라이트" : "다크";

  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={toggleTheme}
      aria-label={`${targetLabel} 테마로 전환`}
      title={`${targetLabel} 테마로 전환`}
    >
      <span aria-hidden="true">{theme === "dark" ? "☀" : "☾"}</span>
      <span>{targetLabel}</span>
    </button>
  );
}
