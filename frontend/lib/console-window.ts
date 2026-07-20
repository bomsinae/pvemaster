export type ConsoleWindowHandle = {
  opener: unknown;
  focus: () => void;
};

export type ConsoleWindowOpener = (
  url: string,
  target: string,
  features: string,
) => ConsoleWindowHandle | null;

const CONSOLE_WINDOW_FEATURES = [
  "popup=yes",
  "width=1280",
  "height=860",
  "resizable=yes",
  "scrollbars=no",
].join(",");

export function consoleWindowUrl(workloadId: string): string {
  return `/console/${encodeURIComponent(workloadId)}`;
}

export function consoleWindowName(workloadId: string): string {
  return `pvemaster-console-${workloadId.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

export function openConsoleWindow(
  workloadId: string,
  opener: ConsoleWindowOpener = window.open.bind(window),
): boolean {
  const popup = opener(
    consoleWindowUrl(workloadId),
    consoleWindowName(workloadId),
    CONSOLE_WINDOW_FEATURES,
  );
  if (!popup) return false;

  try {
    popup.opener = null;
  } catch {
    // Some browsers expose a read-only opener. The console is same-origin.
  }
  popup.focus();
  return true;
}
