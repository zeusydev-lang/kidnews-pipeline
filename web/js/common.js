// Shared helpers for every page.
//
// Story text comes from an AI model, so it is treated as untrusted:
// everything is added with textContent (never innerHTML), and links and
// images are only used if they are http(s) or a local images/ path.

/** Create an element: el("p", { class: "lead" }, "Hello", otherNode) */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

/** Return the URL if it's safe to link to or load, otherwise null. */
export function safeUrl(url) {
  if (typeof url !== "string" || !url) return null;
  if (/^images\/[\w./-]+$/.test(url) && !url.includes("..")) return url; // local copy
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}

/** Slugs are lowercase letters, numbers and single hyphens (same rule as the database). */
export function isValidSlug(slug) {
  return typeof slug === "string" && slug.length <= 80 && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug);
}

export async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-cache" });
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
}

/** "2026-11-11T18:59:42Z" -> "November 11, 2026" */
export function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric", timeZone: "UTC" });
}

/** Split body text into paragraphs on blank lines. */
export function paragraphs(text) {
  return String(text || "")
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean);
}
