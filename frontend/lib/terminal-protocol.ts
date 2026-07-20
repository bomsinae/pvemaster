const encoder = new TextEncoder();

export function terminalInputFrame(data: string): string {
  return `0:${encoder.encode(data).byteLength}:${data}`;
}

export function terminalResizeFrame(cols: number, rows: number): string {
  return `1:${cols}:${rows}:`;
}

export function terminalPingFrame(): "2" {
  return "2";
}

export function consumeTerminalHandshake(output: Uint8Array): Uint8Array | null {
  if (output[0] !== 79 || output[1] !== 75) return null;
  return output.slice(2);
}
