// Shared UI primitives: DOM builder, API client, toasts, modal, drawer, form helpers.

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "value") el.value = v;
    else if (k === "checked" || k === "disabled" || k === "selected") el[k] = Boolean(v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  append(el, children);
  return el;
}

// Null-safe append (DOM append() would render null as the text "null").
export function mount(el, ...children) { append(el, children); return el; }

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

const ICONS = {
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4 6h.01M4 12h.01M4 18h.01"/>',
  map: '<rect x="3" y="4" width="5" height="16" rx="1"/><rect x="10" y="4" width="5" height="10" rx="1"/><rect x="17" y="4" width="4" height="13" rx="1"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1"/><path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"/>',
  download: '<path d="M12 4v11m0 0-4-4m4 4 4-4"/><path d="M4 19h16"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
  activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  sparkles: '<path d="M12 3l1.8 4.7L18.5 9.5 13.8 11.3 12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
  check: '<path d="M5 12l5 5L20 7"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  alert: '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  upload: '<path d="M12 20V9m0 0-4 4m4-4 4 4"/><path d="M4 5h16"/>',
  home: '<path d="M3 11 12 4l9 7"/><path d="M5 10v10h14V10"/>',
  chat: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18.5 20a6.5 6.5 0 0 0-3-5.5"/>',
  play: '<path d="M7 5v14l11-7z"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
};

export function icon(name) {
  const span = document.createElement("span");
  span.style.display = "inline-flex";
  // Trusted static markup from the ICONS table above (never user data).
  span.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' + (ICONS[name] || "") + "</svg>";
  return span.firstChild;
}

// ---- API ----------------------------------------------------------------

// Set by main.js: called when the server says the session is gone (e.g. SSO idle timeout).
let onUnauthorized = null;
export const setUnauthorizedHandler = (fn) => { onUnauthorized = fn; };

async function request(method, path, body, isForm) {
  const opts = { method, headers: { "X-Requested-With": "fabulist" } };
  if (body !== undefined) {
    if (isForm) opts.body = body;
    else { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  }
  let r;
  try { r = await fetch(path, opts); } catch (_) {
    // fetch() only rejects when no response came back (offline, dropped connection, server down); its own message ("Failed to fetch") means nothing to a user.
    throw new Error("Can't reach the server. Check your connection and try again.");
  }
  if (!r.ok) {
    if (r.status === 401 && onUnauthorized) onUnauthorized();
    // A 401 in the browser means the sign-in lapsed: say what to do rather than the bare "Not authenticated".
    let msg = r.status === 401 ? "Your sign-in has expired. Reload the page to sign in again." : r.status + " " + r.statusText;
    if (r.status !== 401) {
      try {
        const j = await r.json();
        if (typeof j.detail === "string") msg = j.detail;
        else if (Array.isArray(j.detail)) msg = j.detail.map((d) => (d.loc ? d.loc.slice(1).join(".") + ": " : "") + d.msg).join("; ");
      } catch (_) { /* non-JSON error body */ }
    }
    throw new Error(msg);
  }
  if (r.status === 204) return null;
  return r.json();
}

export const api = {
  get: (p) => request("GET", p),
  post: (p, b) => request("POST", p, b === undefined ? {} : b),
  put: (p, b) => request("PUT", p, b),
  patch: (p, b) => request("PATCH", p, b),
  del: (p) => request("DELETE", p),
  upload: (p, form) => request("POST", p, form, true),
};

// ---- feedback -----------------------------------------------------------

let toastBox;
export function toast(message, kind = "") {
  if (!toastBox) { toastBox = h("div", { class: "toasts", role: "status" }); document.body.append(toastBox); }
  const t = h("div", { class: "toast " + kind, role: kind === "error" ? "alert" : null }, message);
  toastBox.append(t);
  setTimeout(() => t.remove(), kind === "error" ? 6000 : 3500);
}

// Runs fn once typing pauses. Re-rendering a table of thousands of rows on every keystroke made typing a filter lag.
export function debounce(fn, ms = 150) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

export function guard(fn) {
  return async (...args) => {
    try { return await fn(...args); } catch (e) { toast(e.message, "error"); }
  };
}

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function layer(panel, onClose, canDismiss) {
  const opener = document.activeElement;
  const overlay = h("div", { class: "overlay" });
  const close = () => {
    overlay.remove(); panel.remove(); document.removeEventListener("keydown", keys);
    if (opener && opener.isConnected && opener.focus) opener.focus();  // return focus where the user was
    onClose && onClose();
  };
  // A click outside or Escape may need to ask first (a form with typing in it); the dialog's own buttons always close at once.
  const dismiss = async () => { if (!canDismiss || (await canDismiss())) close(); };
  const keys = (e) => {
    if (!panel.isConnected || panel !== [...document.querySelectorAll(".modal, .drawer")].pop()) return;  // topmost layer only
    if (e.key === "Escape") { e.stopPropagation(); dismiss(); return; }
    if (e.key !== "Tab") return;
    const items = [...panel.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
  overlay.addEventListener("click", dismiss);
  document.addEventListener("keydown", keys);
  document.body.append(overlay, panel);
  return close;
}

let layerSeq = 0;

export function modal({ title, body, actions = [], wide = false, description }) {
  const foot = h("div", { class: "modal-foot" });
  const titleId = "dlg-" + ++layerSeq;
  const panel = h("div", { class: "modal" + (wide ? " wide" : ""), role: "dialog", "aria-modal": "true", "aria-labelledby": titleId },
    h("div", { class: "modal-head" }, h("h2", { id: titleId }, title), description ? h("p", { class: "muted small", style: { marginTop: "4px" } }, description) : null),
    h("div", { class: "modal-body" }, body), foot);
  let typed = false;  // only forms (dialogs with actions) can hold work worth protecting
  const close = layer(panel, null, () => !typed || confirmBox("What you typed in this dialog will be lost.", { okLabel: "Discard", title: "Close without saving?" }));
  if (actions.length) panel.addEventListener("input", () => { typed = true; }), panel.addEventListener("change", () => { typed = true; });
  for (const a of actions) {
    foot.append(h("button", { class: "btn " + (a.kind || ""), onclick: guard(async () => { if ((await a.onClick()) !== false) close(); }) }, a.label));
  }
  foot.prepend(h("button", { class: "btn btn-ghost", onclick: close }, actions.length ? "Cancel" : "Close"));
  const first = panel.querySelector("input, textarea, select") || foot.querySelector(".btn-primary") || foot.querySelector("button");
  if (first) setTimeout(() => first.focus(), 30);
  return close;
}

export function confirmBox(message, { okLabel = "Confirm", danger = false, title = "Are you sure?" } = {}) {
  return new Promise((resolve) => {
    let ok = false;
    const panel = h("div", { class: "modal", role: "alertdialog" },
      h("div", { class: "modal-head" }, h("h2", {}, title)),
      h("div", { class: "modal-body" }, h("p", { class: "muted" }, message)),
      h("div", { class: "modal-foot" },
        h("button", { class: "btn btn-ghost", onclick: () => close() }, "Cancel"),
        h("button", { class: "btn " + (danger ? "btn-danger" : "btn-primary"), onclick: () => { ok = true; close(); } }, okLabel)));
    const close = layer(panel, () => resolve(ok));
    setTimeout(() => panel.querySelector(".modal-foot button:last-child").focus(), 30);
  });
}

export function drawer({ title, subtitle, body, footer, onkeydown }) {
  const titleId = "dlg-" + ++layerSeq;
  const panel = h("aside", { class: "drawer", role: "dialog", "aria-modal": "true", "aria-labelledby": titleId, onkeydown },
    h("div", { class: "drawer-head" },
      h("div", { class: "grow" }, subtitle ? h("div", { class: "muted small" }, subtitle) : null, h("h2", { id: titleId }, title)),
      h("button", { class: "btn btn-ghost icon-btn", "aria-label": "Close", onclick: () => close() }, icon("x"))),
    h("div", { class: "drawer-body" }, body),
    footer ? h("div", { class: "drawer-foot" }, footer) : null);
  const close = layer(panel);
  const first = panel.querySelector(".drawer-body " + FOCUSABLE) || panel.querySelector("button");
  if (first) setTimeout(() => first.focus(), 30);
  return close;
}

// ---- display helpers ----------------------------------------------------

export function badge(text, tone = "") { return h("span", { class: "badge " + tone }, text); }

export const statusTone = { draft: "", approved: "green", excluded: "red", rejected: "red", running: "blue", done: "green", failed: "red", cancelled: "amber" };
export const typeTone = { business: "violet", functional: "blue", "non-functional": "amber", constraint: "red", rule: "green" };

export function scorePill(score) {
  const tone = score >= 80 ? "good" : score >= 55 ? "warn" : "bad";
  return h("span", { class: "score " + tone, title: "Quality score" }, String(score));
}

export function meter(pct, tone) {
  const t = tone || (pct >= 80 ? "good" : pct >= 50 ? "warn" : "bad");
  return h("div", { class: "meter " + t }, h("span", { style: { width: Math.max(0, Math.min(100, pct)) + "%" } }));
}

export function issueIcon(severity) {
  const name = severity === "error" ? "alert" : severity === "warning" ? "alert" : "info";
  const el = icon(name);
  el.style.color = severity === "error" ? "var(--red)" : severity === "warning" ? "var(--amber)" : "var(--blue)";
  el.style.flex = "none"; el.style.marginTop = "2px";
  return el;
}

export function issueList(issues) {
  if (!issues.length) return h("div", { class: "callout ok" }, icon("check"), "No quality issues found.");
  return h("div", { class: "issue-list" }, issues.map((i) => h("div", { class: "issue" }, issueIcon(i.severity), h("span", {}, i.message))));
}

export function empty({ iconName = "sparkles", title, text, action }) {
  return h("div", { class: "empty" }, h("div", { class: "icon" }, icon(iconName)), h("h3", {}, title), text ? h("p", {}, text) : null, action || null);
}

export function timeAgo(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}

export const fmt = (n) => Number(n || 0).toLocaleString();
export const duration = (ms) => (!ms ? "–" : ms < 1000 ? ms + " ms" : (ms / 1000).toFixed(1) + " s");

// ---- form helpers -------------------------------------------------------

let fieldSeq = 0;
export function field(label, control, hint, cls = "") {
  const labelEl = h("label", {}, label);
  const hintEl = hint ? h("div", { class: "hint" }, hint) : null;
  // Tie the label (and hint) to the control so it has an accessible name and clicking the label
  // focuses it. Wrappers holding exactly one form control are linked to that control.
  const CONTROLS = "input, select, textarea";
  const inner = control instanceof Element ? (control.matches(CONTROLS) ? [control] : control.querySelectorAll(CONTROLS)) : [];
  if (inner.length === 1) {
    const el = inner[0];
    if (!el.id) el.id = "fld-" + ++fieldSeq;
    labelEl.htmlFor = el.id;
    if (hintEl) { hintEl.id = el.id + "-hint"; el.setAttribute("aria-describedby", hintEl.id); }
  } else if (control instanceof Element) {  // e.g. a segmented button group: label it as a group
    labelEl.id = "fld-" + ++fieldSeq + "-label";
    if (!control.getAttribute("role")) control.setAttribute("role", "group");
    control.setAttribute("aria-labelledby", labelEl.id);
  }
  return h("div", { class: "field " + cls }, labelEl, control, hintEl);
}

// Inline required-field validation: marks the control invalid, shows the message beside it
// (read with the control), focuses it, and throws so guard() also announces it.
export function requireValue(control, message) {
  if (control.value.trim()) return;
  const errId = (control.id || (control.id = "fld-" + ++fieldSeq)) + "-err";
  let err = document.getElementById(errId);
  if (!err) {
    err = h("div", { class: "field-error", id: errId }, message);
    control.after(err);
    control.setAttribute("aria-describedby", [control.getAttribute("aria-describedby"), errId].filter(Boolean).join(" "));
    control.addEventListener("input", () => {
      control.removeAttribute("aria-invalid"); err.remove();
      control.setAttribute("aria-describedby", control.getAttribute("aria-describedby").replace(errId, "").trim());
      if (!control.getAttribute("aria-describedby")) control.removeAttribute("aria-describedby");
    }, { once: true });
  }
  control.setAttribute("aria-invalid", "true");
  control.focus();
  throw new Error(message);
}

export function select(options, value, attrs = {}) {
  return h("select", attrs, options.map(([v, l]) => h("option", { value: v, selected: String(v) === String(value) }, l)));
}

export function linesInput(values, rows = 4, placeholder = "") {
  return h("textarea", { rows, placeholder, value: (values || []).join("\n") });
}
export const readLines = (ta) => ta.value.split("\n").map((s) => s.trim()).filter(Boolean);

// Tag input: comma/Enter adds a chip; suggestions via datalist.
export function tagsInput(values, suggestions = []) {
  let tags = [...(values || [])];
  const listId = "dl-" + Math.random().toString(36).slice(2);
  const chips = h("div", { class: "row wrap" });
  const input = h("input", { type: "text", list: listId, placeholder: "Type and press Enter" });
  const render = () => chips.replaceChildren(...tags.map((t) => h("span", { class: "chip" }, t,
    h("button", { type: "button", "aria-label": "Remove " + t, onclick: () => { tags = tags.filter((x) => x !== t); render(); } }, "×"))));
  const add = () => {
    for (const v of input.value.split(",").map((s) => s.trim()).filter(Boolean)) if (!tags.includes(v)) tags.push(v);
    input.value = ""; render();
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); } });
  input.addEventListener("change", add);
  render();
  const el = h("div", { class: "stack", style: { gap: "6px" } }, chips, input, h("datalist", { id: listId }, suggestions.map((s) => h("option", { value: s }))));
  return { el, get: () => { add(); return tags; } };
}

export function segmented(options, value, onChange) {
  const wrap = h("div", { class: "seg", role: "group" });
  const render = (v) => wrap.replaceChildren(...options.map(([val, label]) =>
    h("button", { type: "button", class: val === v ? "on" : "", "aria-pressed": String(val === v), onclick: () => { render(val); onChange(val); } }, label)));
  render(value);
  return wrap;
}

// "Who can see this project": everyone, or only people chosen from the named accounts (administrators always can).
// `people` is [{username, role}]; returns {el, value()} with value() = {visibility, members}.
export function accessPicker(people, visibility = "everyone", members = []) {
  let mode = visibility;
  const tags = tagsInput(members, people.map((p) => p.username));
  const detail = h("div", { class: "stack", style: { gap: "6px" } }, tags.el,
    h("div", { class: "muted small" }, "People keep their own role: a viewer stays read-only. Administrators can always see every project, and you stay in."));
  const show = () => { detail.hidden = mode !== "members"; };
  const el = h("div", { class: "stack", style: { gap: "8px" } },
    segmented([["everyone", "Everyone"], ["members", "Only people I choose"]], mode, (v) => { mode = v; show(); }), detail);
  show();
  return { el, value: () => ({ visibility: mode, members: tags.get() }) };
}

export function download(url) {
  const a = h("a", { href: url, download: "" });
  document.body.append(a); a.click(); a.remove();
}

// A region that scrolls sideways must be reachable by keyboard (WCAG 2.1.1), otherwise keyboard users can never read its
// far columns. Only regions that really overflow get a tab stop, and they lose it again when the window widens.
export function markScrollRegions(root = document) {
  root.querySelectorAll(".table-wrap").forEach((el) => {
    const scrolls = el.scrollWidth > el.clientWidth + 1;
    if (scrolls && !el.dataset.scrollRegion) {
      el.dataset.scrollRegion = "1";
      el.tabIndex = 0;
      el.setAttribute("role", "region");
      const title = el.closest(".card")?.querySelector("h3")?.textContent?.trim();
      el.setAttribute("aria-label", (title || "Table") + " (scrolls sideways)");
    } else if (!scrolls && el.dataset.scrollRegion) {
      delete el.dataset.scrollRegion;
      el.removeAttribute("tabindex"); el.removeAttribute("role"); el.removeAttribute("aria-label");
    }
  });
}

// ---- change history -----------------------------------------------------

function short(v) {
  if (v === null || v === undefined || v === "") return "∅";
  if (Array.isArray(v)) return v.length + " item" + (v.length === 1 ? "" : "s");
  const t = String(v);
  return t.length > 60 ? t.slice(0, 57) + "…" : t;
}

// Collapses a bulk action (same actor, action and field changes within a minute) into one row.
function groupBulk(entries) {
  const groups = [];
  for (const e of entries) {
    const key = [e.actor, e.action, e.item_type, JSON.stringify(e.changes || {}), (e.created || "").slice(0, 16)].join("|");
    const last = groups[groups.length - 1];
    if (last && last.key === key) last.items.push(e); else groups.push({ key, items: [e] });
  }
  return groups.map((g) => g.items.length < 3 ? g.items : [{ ...g.items[0], item_key: g.items.length + " items (" + g.items[g.items.length - 1].item_key + " – " + g.items[0].item_key + ")", summary: "" }]).flat();
}

export function historyList(entries, { showItem = false, group = showItem } = {}) {
  if (!entries.length) return h("p", { class: "muted small" }, "No changes recorded yet.");
  return h("div", { class: "stack", style: { gap: "10px" } }, (group ? groupBulk(entries) : entries).map((e) => {
    const fields = Object.entries(e.changes || {});
    return h("div", { class: "row", style: { alignItems: "flex-start" } },
      h("span", { class: "dot", style: { marginTop: "7px", background: e.action === "deleted" ? "var(--red)" : e.action === "created" ? "var(--green)" : "var(--accent)" } }),
      h("div", { class: "grow small" },
        h("div", {}, h("b", {}, e.actor), " " + e.action + " ", showItem ? h("span", { class: "mono" }, e.item_key) : null,
          h("span", { class: "muted" }, " · " + timeAgo(e.created))),
        showItem && e.summary ? h("div", { class: "muted" }, e.summary) : null,
        fields.length ? h("div", { class: "muted" }, fields.map(([k, c], i) => [i ? "; " : "",
          k.replace(/_/g, " ") + ": " + short(c.from) + " → " + short(c.to)])) : null));
  }));
}
