// Dual-mode authentication for Databricks REST APIs.
//
// Resolution order (first that applies wins):
//   1. Service Principal OAuth M2M  -> DATABRICKS_CLIENT_ID + DATABRICKS_CLIENT_SECRET
//      This is what the Databricks Apps runtime injects when deployed.
//   2. Static bearer token          -> DATABRICKS_TOKEN (PAT or short-lived OAuth token)
//   3. CLI profile fallback (local) -> `databricks auth token --profile <profile>`
//
// Tokens are cached in-memory and refreshed shortly before expiry.

import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { config } from './config.js';

const execFileP = promisify(execFile);

let cached = null; // { token, expiresAt (ms epoch) }
const SKEW_MS = 60 * 1000; // refresh 60s before expiry

function mode() {
  if (process.env.DATABRICKS_CLIENT_ID && process.env.DATABRICKS_CLIENT_SECRET) return 'oauth-m2m';
  if (process.env.DATABRICKS_TOKEN) return 'static-token';
  return 'cli-profile';
}

export function authMode() {
  return mode();
}

async function fetchOAuthM2M() {
  const clientId = process.env.DATABRICKS_CLIENT_ID;
  const clientSecret = process.env.DATABRICKS_CLIENT_SECRET;
  const url = `${config.host}/oidc/v1/token`;
  const basic = Buffer.from(`${clientId}:${clientSecret}`).toString('base64');
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      Authorization: `Basic ${basic}`,
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: new URLSearchParams({ grant_type: 'client_credentials', scope: 'all-apis' }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`OAuth M2M token request failed (${res.status}): ${text.slice(0, 300)}`);
  }
  const data = await res.json();
  const expiresInMs = (data.expires_in ? Number(data.expires_in) : 3600) * 1000;
  return { token: data.access_token, expiresAt: Date.now() + expiresInMs };
}

async function fetchProfileToken() {
  // Local dev only: shell out to the Databricks CLI. Never used in production.
  const { stdout } = await execFileP('databricks', ['auth', 'token', '--profile', config.profile], {
    timeout: 20000,
  });
  const data = JSON.parse(stdout);
  const expiry = data.expiry ? Date.parse(data.expiry) : Date.now() + 30 * 60 * 1000;
  return { token: data.access_token, expiresAt: Number.isFinite(expiry) ? expiry : Date.now() + 30 * 60 * 1000 };
}

export async function getToken() {
  const now = Date.now();
  if (cached && cached.token && cached.expiresAt - SKEW_MS > now) {
    return cached.token;
  }

  const m = mode();
  if (m === 'static-token') {
    // Static tokens have no known expiry here; cache for 30 min then re-read env.
    cached = { token: process.env.DATABRICKS_TOKEN, expiresAt: now + 30 * 60 * 1000 };
  } else if (m === 'oauth-m2m') {
    cached = await fetchOAuthM2M();
  } else {
    cached = await fetchProfileToken();
  }
  return cached.token;
}

export function authHeaders(token) {
  return { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
}
