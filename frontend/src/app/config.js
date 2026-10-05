/** Public, build-selected API endpoints and bounded browser recovery timings. */
export const JOB_API_URL = import.meta.env.VITE_JOB_API_URL?.replace(/\/$/, '');
export const WEBSOCKET_URL = import.meta.env.VITE_WEBSOCKET_URL;
export const POLL_INTERVAL_MS = 5_000;
export const WEBSOCKET_HEARTBEAT_INTERVAL_MS = 120_000;
export const RECONNECT_MAX_DELAY_MS = 30_000;
export const JOB_REFRESH_BACKOFF_INITIAL_MS = 5_000;
export const JOB_REFRESH_BACKOFF_MAX_MS = 60_000;
export const MAX_SOURCE_UPLOAD_BYTES = 256 * 1024 * 1024;
