export function isBrowserDownloadUri(uri?: string): uri is string {
  return Boolean(uri && (uri.startsWith("/api/") || uri.startsWith("https://") || uri.startsWith("http://")));
}
