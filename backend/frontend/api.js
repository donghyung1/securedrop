"use strict";
async function api(path, options = {}) {
  const response = await fetch(window.SECUREDROP_API_BASE + path, {
    ...options, cache: "no-store", referrerPolicy: "no-referrer",
  });
  const data = await response.json();
  if (!response.ok) throw Object.assign(new Error(data.error?.message || `요청 실패 (${response.status})`), { data, status: response.status });
  return data;
}
