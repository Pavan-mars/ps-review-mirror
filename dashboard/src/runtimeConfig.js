// =====================================================================
// src/runtimeConfig.js -- ONE image, MANY environments.
//
// Vite bakes import.meta.env.* into the bundle at BUILD time, so a
// VITE_API_BASE_URL baked for dev makes an image that can never be
// promoted to UAT or prod. That is a release-process problem, not a
// config problem, and it only shows up the first time someone tries to
// promote.
//
// So the base URL is resolved at BOOT instead. index.html loads
// /config.js as a CLASSIC script BEFORE the module bundle -- classic
// scripts run synchronously and to completion first, so
// window.__APP_CONFIG__ is populated before any module here evaluates.
// No async, no app-start gate, no race.
//
// In the container an entrypoint script rewrites /config.js from the
// API_BASE_URL environment variable on every start. Locally there is a
// no-op public/config.js and the Vite env still works as the fallback,
// so `npm run dev` is unchanged.
//
// PRECEDENCE: runtime config  >  build-time env  >  empty.
// Empty is deliberate -- the app's live-only pattern shows an explicit
// "no API base URL configured" rather than silently calling same-origin.
// =====================================================================
const runtime =
  (typeof window !== 'undefined' && window.__APP_CONFIG__ && window.__APP_CONFIG__.apiBaseUrl) || '';

const buildTime = (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.VITE_API_BASE_URL) || '';

export const API_BASE_URL = String(runtime || buildTime || '').replace(/\/$/, '');

// Which one won -- worth logging once, because "the dashboard is pointing
// at the wrong API" is otherwise a 20-minute investigation.
export const API_BASE_SOURCE = runtime ? 'runtime /config.js' : (buildTime ? 'build-time VITE_API_BASE_URL' : 'unset');

// Write-route token. Runtime only (never baked): the entrypoint writes it
// into /config.js from MUTATION_TOKEN on the task definition. Empty means
// the API is running without a token and the header is simply omitted.
export const MUTATION_TOKEN =
  (typeof window !== 'undefined' && window.__APP_CONFIG__ && window.__APP_CONFIG__.mutationToken) || '';

export default API_BASE_URL;
