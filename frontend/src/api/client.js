/**
 * frontend/src/api/client.js
 * Central API client reading VITE_API_BASE_URL.
 * Provides clear readable errors with HTTP status and body on API error,
 * and "Cannot reach the server at <url>" when connection cannot be established.
 */

export const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL !== undefined
    ? import.meta.env.VITE_API_BASE_URL
    : (import.meta.env.DEV ? 'http://127.0.0.1:8000' : '/api')
).replace(/\/$/, '');

export function getDemoAccessToken() {
  return import.meta.env.VITE_DEMO_ACCESS_TOKEN || localStorage.getItem('demo_access_token') || '';
}

export function setDemoAccessToken(token) {
  if (token) {
    localStorage.setItem('demo_access_token', token);
  } else {
    localStorage.removeItem('demo_access_token');
  }
}

export async function apiFetch(endpoint, options = {}) {
  const url = endpoint.startsWith('http')
    ? endpoint
    : `${API_BASE_URL}${endpoint.startsWith('/') ? endpoint : `/${endpoint}`}`;

  const token = getDemoAccessToken();
  const headers = new Headers(options.headers || {});
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
    headers.set('X-Demo-Access-Token', token);
  }

  let response;
  try {
    response = await fetch(url, { ...options, headers });
  } catch (networkError) {
    throw new Error(`Cannot reach the server at ${url}`);
  }

  if (!response.ok) {
    let errorDetail = '';
    try {
      const json = await response.json();
      errorDetail = json.detail || json.message || JSON.stringify(json);
    } catch {
      try {
        errorDetail = await response.text();
      } catch {
        errorDetail = response.statusText;
      }
    }
    const error = new Error(`API Error [HTTP ${response.status}]: ${errorDetail || response.statusText}`);
    error.status = response.status;
    error.body = errorDetail;
    throw error;
  }

  return response;
}

export async function apiJson(endpoint, options = {}) {
  const res = await apiFetch(endpoint, options);
  return res.json();
}
