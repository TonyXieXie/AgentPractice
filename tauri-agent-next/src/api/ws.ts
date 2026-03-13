export function buildWebSocketUrl(baseUrl: string): string {
  return `${baseUrl.replace(/^http/i, (value) => (value.toLowerCase() === "https" ? "wss" : "ws"))}/ws`;
}
