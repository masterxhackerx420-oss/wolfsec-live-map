// Runtime settings. On GitHub Pages the app runs without a server: layers in
// BROWSER_LIVE are fetched straight from the source by your browser; all others
// come from data/<layer>.json, refreshed by the GitHub Actions workflow.
window.LIVEMAP_CONFIG = {
  defaultRefreshSec: 60,
  // Set to false to always use the Actions snapshots, even for CORS-friendly feeds.
  browserLive: true,
  repoUrl: "https://github.com/masterxhackerx420-oss/wolfsec-live-map",
};
