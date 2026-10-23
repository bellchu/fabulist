// App shell, routing and the projects home view.
import { h, mount, icon, api, guard, toast, modal, field, requireValue, select, empty, badge, timeAgo, meter, setUnauthorizedHandler, markScrollRegions, accessPicker, guardLeaving, hasPendingEdits, confirmBox } from "./core.js";
import { renderProject } from "./project.js";
import { renderAdmin } from "./admin.js";
import { renderAccount } from "./account.js";
import { renderSearch } from "./search.js";

export const state = { meta: null, projects: [], me: null };

function navItem(href, label, active, iconName) {
  return h("a", { class: "nav-item" + (active ? " active" : ""), href, "aria-current": active ? "page" : null }, iconName ? icon(iconName) : null, label);
}

// One input for the life of the page: the header is rebuilt on every route, and a new input would lose half-typed text and the cursor.
let searchInput = null;

function globalSearch(route) {
  const listId = "project-search";
  if (!searchInput) {
    searchInput = h("input", { type: "search", list: listId, placeholder: "Search projects, requirements, stories…  ( / )", "aria-label": "Search projects, requirements and stories", "aria-keyshortcuts": "/" });
    // "/" jumps to the search box from anywhere, except while typing in a field (or with a dialog open: its fields are fields too)
    document.addEventListener("keydown", (e) => {
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
      const t = e.target;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      if (document.querySelector(".modal, .drawer")) return;
      e.preventDefault();
      searchInput.focus();
    });
    searchInput.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      const text = searchInput.value.trim();
      const hit = state.projects.find((p) => p.name.toLowerCase() === text.toLowerCase());
      if (hit) { searchInput.value = ""; searchInput.blur(); }
      location.hash = hit ? "#/p/" + hit.id : "#/search/" + encodeURIComponent(text);  // an exact project name opens it; anything else is a search
    });
  }
  const typing = document.activeElement === searchInput;
  if (typing) setTimeout(() => { searchInput.focus(); searchInput.setSelectionRange(searchInput.value.length, searchInput.value.length); }, 0);  // re-attaching drops the focus
  else if (route[0] !== "search") searchInput.value = "";  // the words of a finished search do not follow you around
  return h("div", { class: "global-search" }, icon("search"), searchInput, h("datalist", { id: listId }, state.projects.map((p) => h("option", { value: p.name }))));
}

function shell(route) {
  const isAdmin = state.me.role === "admin";
  const current = route[0] === "p" ? state.projects.find((p) => p.id === route[1]) : null;
  const header = h("header", { class: "global-header" },
    h("a", { class: "app-logo", href: "#/" }, h("span", { class: "brand-mark" }, "F"), "Fabulist"),
    globalSearch(route),
    h("div", { class: "user-chip" },
      state.me.role === "viewer" ? badge("Read-only", "amber") : null,
      isAdmin ? h("a", { class: "btn btn-ghost icon-btn", href: "#/admin/overview", title: "Setup", "aria-label": "Setup" }, icon("settings")) : null,
      state.me.username === "anonymous"
        ? h("span", { class: "muted small", title: "Authentication is not configured on this deployment" }, "Sign-in disabled")
        : h("a", { class: "muted small", href: "#/account", title: "Account and API tokens" }, state.me.username === state.me.role ? state.me.username : state.me.username + " · " + state.me.role),
      h("span", { class: "avatar", title: state.me.username }, (state.me.username || "?").slice(0, 2))));
  const nav = h("nav", { class: "context-bar", "aria-label": "Main" },
    h("a", { class: "waffle", href: "#/", title: "App launcher", "aria-label": "Home" }, h("span", {}, Array.from({ length: 9 }, () => h("i")))),
    h("span", { class: "app-name" }, "Story Studio"),
    navItem("#/", "Projects", route[0] === "" || (route[0] === "p" && !current)),
    current ? navItem("#/p/" + current.id, current.name, true, "folder") : null,
    isAdmin ? navItem("#/admin/overview", "Setup", route[0] === "admin") : null);
  const content = h("div", { class: "content", id: "content", tabindex: "-1" }, h("div", { class: "crumbs", id: "crumbs" }));
  const skip = h("a", { class: "skip-link", href: "#content", onclick: (e) => { e.preventDefault(); content.focus(); } }, "Skip to content");
  document.getElementById("app").replaceChildren(h("div", { class: "shell" }, skip, header, nav, h("main", { class: "main" }, content),
    h("footer", { class: "app-footer" }, "Fabulist " + state.meta.version + (state.meta.support_contact ? " · Support: " + state.meta.support_contact : ""))));  // support tickets quote the version
  return content;
}

export function setCrumbs(...parts) {
  const el = document.getElementById("crumbs");
  const out = [];
  parts.forEach((p, i) => {
    if (i) out.push(h("span", {}, "/"));
    out.push(typeof p === "string" ? h("strong", {}, p) : h("a", { href: p.href }, p.label));
  });
  el.replaceChildren(...out);
  // The page title says where you are (tabs, history, screen readers): the deepest crumb first, then the ones above it.
  setTitle(...parts.map((p) => (typeof p === "string" ? p : p.label)).reverse());
}

export function setTitle(...names) {
  document.title = [...names.filter(Boolean), "Fabulist"].join(" · ");
}

export async function refreshProjects() {
  state.projects = await api.get("/api/projects");
}

// Lives outside #app, which every route replaces.
const busyBar = h("div", { class: "route-busy", hidden: true });
document.body.prepend(busyBar);
function setBusy(on) {
  busyBar.hidden = !on;
  document.getElementById("app").toggleAttribute("aria-busy", on);
}

// A selector for the focused control, so a re-render of the same view (e.g. after saving in
// a drawer) can put keyboard focus back instead of dropping it to the top of the page.
function focusKey() {
  const el = document.activeElement;
  if (!el || !el.closest("#app")) return null;
  const label = el.getAttribute("aria-label");
  if (label) return el.tagName.toLowerCase() + '[aria-label="' + CSS.escape(label) + '"]';
  // Generated field ids (fld-N) change on every render, so they can't identify a control.
  return el.id && !el.id.startsWith("fld-") ? "#" + CSS.escape(el.id) : null;
}

let rendering = 0;
// A screen reader cannot see that a navigation changed the page: a polite live region, outside the part that is rebuilt on every route, says where it led.
const announcer = h("div", { id: "route-announcer", class: "sr-only", "aria-live": "polite", "aria-atomic": "true" });
document.body.append(announcer);
let announcedHash = null;
function announceRoute() {
  if (location.hash === announcedHash) return;  // a redraw of the same screen is not news
  announcedHash = location.hash;
  announcer.textContent = "";
  setTimeout(() => { announcer.textContent = document.title; }, 50);
}

async function route() {
  const ticket = ++rendering;
  guardLeaving(null);  // each page registers its own
  const hash = location.hash, refocus = focusKey();
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  // Delayed so fast navigations don't flash; slow ones show the old page is being replaced.
  const busy = setTimeout(() => setBusy(true), 150);
  try {
    if (!state.meta) [state.meta, state.me] = await Promise.all([api.get("/api/meta"), api.get("/api/me")]);
    if (state.me.must_change) {  // an administrator chose this password: nothing else works until its owner replaces it
      state.projects = [];
      if (parts[0] !== "account") { location.hash = "#/account"; return; }
    } else await refreshProjects();
    if (ticket !== rendering) return;
    const content = shell(parts);
    if (parts[0] === "p" && parts[1]) await renderProject(content, parts[1], parts[2] || "overview");
    else if (parts[0] === "admin" && state.me.role === "admin") await renderAdmin(content, parts[1] || "overview");
    else if (parts[0] === "account") await renderAccount(content);
    else if (parts[0] === "search") await renderSearch(content, decodeURIComponent(parts.slice(1).join("/")));
    else renderHome(content);
    if (ticket === rendering) announceRoute();
    if (refocus && ticket === rendering && location.hash === hash && (!document.activeElement || document.activeElement === document.body)) {
      document.querySelector("#app " + refocus)?.focus();
    }
  } catch (e) {
    if (ticket !== rendering) return;
    const error = h("div", { class: "callout error", role: "alert" }, h("span", { class: "grow" }, e.message),
      h("button", { class: "btn btn-sm", onclick: route }, "Retry"), h("a", { href: "#/" }, "Back to projects"));
    // Keep the header and navigation when we can, so a stale link isn't a dead end.
    if (state.me) mount(shell(parts), error);
    else document.getElementById("app").replaceChildren(h("div", { class: "content" }, error));
  } finally {
    clearTimeout(busy);
    if (ticket === rendering) setBusy(false);
  }
}

let archivedList = null;  // null: showing active projects
const FILTER_FROM = 8;  // show the filter bar from this many projects
const homeFilter = { q: "", platform: "", sort: "new" };  // kept while navigating, so coming back to the list keeps the filter

function renderHome(content, keepView = false) {
  setCrumbs("Projects");
  if (keepView) content.replaceChildren(content.querySelector("#crumbs"));  // mount() appends: drop the previous view
  else archivedList = null;
  const list = archivedList || state.projects;
  const toggle = h("button", { class: "btn", onclick: guard(async () => {
    archivedList = archivedList ? null : await api.get("/api/projects?archived=true");
    renderHome(content, true);
  }) }, archivedList ? "Back to projects" : "Archived");
  const head = h("div", { class: "page-head" },
    h("span", { class: "object-icon project" }, icon("folder")),
    h("div", { class: "grow" }, h("div", { class: "object-label" }, archivedList ? "Archived projects" : "Projects"), h("h1", {}, archivedList ? "Archived" : "All projects"),
      h("p", { class: "sub" }, list.length + (list.length === 1 ? " item" : " items") + (archivedList
        ? " · Read-only. Open a project to restore it." : " · Each project turns BRDs into reviewed requirements and a traceable backlog."))),
    h("div", { class: "row" }, toggle, state.me.role === "viewer" ? null : importButton(), state.me.role === "viewer" ? null : h("button", { class: "btn btn-primary", onclick: guard(newProjectDialog) }, "New")));
  if (!list.length) {
    mount(content, head, h("div", { class: "card" }, archivedList
      ? empty({ iconName: "folder", title: "No archived projects", text: "Archived projects are kept read-only, with their audit trail, until an administrator deletes them." })
      : empty({
        iconName: "folder", title: "No projects yet",
        text: "Create a project for a business application (e.g. CRM order capture), then upload its BRDs.",
        action: h("span", { class: "row" }, h("button", { class: "btn btn-primary", onclick: guard(newProjectDialog) }, icon("plus"), "Create project"),
          state.me.role === "viewer" ? null : h("button", { class: "btn", title: "A small BRD to try the workflow with: no AI provider is needed", onclick: guard(async () => {
            const p = await api.post("/api/projects/sample");
            location.hash = "#/p/" + p.id + "/sources";
          }) }, "Try a sample project")) })));
    return;
  }
  const card = (p) => {
    const pct = p.requirements ? Math.round((100 * p.requirements_approved) / p.requirements) : 0;
    return h("a", { class: "card project-card", href: "#/p/" + p.id },
      h("div", { class: "row" }, h("h3", { class: "grow" }, p.name), p.visibility === "members" ? badge("Private", "amber") : null, badge(state.meta.platforms[p.platform] || p.platform, "accent")),
      p.description ? h("p", { class: "muted small" }, p.description) : null,
      h("div", { class: "kv" },
        h("div", {}, h("b", {}, p.documents), h("span", {}, "BRDs")),
        h("div", {}, h("b", {}, p.requirements), h("span", {}, "Requirements")),
        h("div", {}, h("b", {}, p.stories), h("span", {}, "Stories"))),
      h("div", {}, h("div", { class: "row small muted" }, h("span", { class: "grow" }, "Requirements reviewed"), pct + "%"), meter(pct, "good")),
      h("div", { class: "muted small" }, "Created " + timeAgo(p.created)));
  };
  const grid = h("div", { class: "grid grid-3" });
  // A filter bar only earns its space once the list is long enough to scroll.
  if (list.length < FILTER_FROM) { grid.append(...list.map(card)); mount(content, head, grid); return; }
  const q = h("input", { type: "search", value: homeFilter.q, placeholder: "Filter by name or description", "aria-label": "Filter projects", style: { maxWidth: "280px" } });
  const platforms = [...new Set(list.map((p) => p.platform))].sort();
  const platform = select([["", "All platforms"], ...platforms.map((k) => [k, state.meta.platforms[k] || k])], homeFilter.platform, { "aria-label": "Platform", style: { width: "auto" } });
  const sort = select([["new", "Newest first"], ["name", "Name A–Z"], ["review", "Most left to review"]], homeFilter.sort, { "aria-label": "Sort projects", style: { width: "auto" } });
  const count = h("span", { class: "muted small", role: "status" });
  const draw = () => {
    Object.assign(homeFilter, { q: q.value, platform: platform.value, sort: sort.value });
    const needle = q.value.trim().toLowerCase();
    const shown = list.filter((p) => (!platform.value || p.platform === platform.value)
      && (!needle || (p.name + " " + (p.description || "")).toLowerCase().includes(needle)));
    const left = (p) => p.requirements - p.requirements_approved;
    shown.sort(sort.value === "name" ? (a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" })
      : sort.value === "review" ? (a, b) => left(b) - left(a) || a.name.localeCompare(b.name)
      : (a, b) => (a.created < b.created ? 1 : -1));
    count.textContent = shown.length === list.length ? list.length + " projects" : shown.length + " of " + list.length + " projects";
    grid.replaceChildren(...(shown.length ? shown.map(card) : [h("div", { class: "card", style: { gridColumn: "1 / -1" } }, empty({
      iconName: "search", title: "No projects match", text: "Try a different name or clear the platform filter." }))]));
  };
  [q, platform, sort].forEach((el) => el.addEventListener(el === q ? "input" : "change", draw));
  mount(content, head, h("div", { class: "row wrap", style: { marginBottom: "12px" } }, q, platform, sort, count), grid);
  draw();
}

// An error nobody caught is a bug in the page itself: say so (at most every few seconds) instead of failing silently. The details stay in the
// browser console; ResizeObserver notices are harmless browser noise.
let lastCrashNotice = 0;
function crashed(event) {
  const text = String((event && (event.message || (event.reason && event.reason.message))) || "");
  if (/ResizeObserver/.test(text) || Date.now() - lastCrashNotice < 5000) return;
  lastCrashNotice = Date.now();
  toast("Something in the page went wrong. If the screen looks wrong, reload it.", "error");
}
window.addEventListener("unhandledrejection", crashed);
window.addEventListener("error", crashed);

// Restores a project from a full archive (Project settings > Download archive) as a new project.
function importButton() {
  const file = h("input", { type: "file", accept: "application/json,.json", hidden: true, "aria-label": "Project archive file" });
  file.addEventListener("change", guard(async () => {
    const chosen = file.files[0];
    file.value = "";
    if (!chosen) return;
    let archive;
    try { archive = JSON.parse(await chosen.text()); } catch (_) { throw new Error("That file is not a valid JSON project archive"); }
    const p = await api.post("/api/projects/import", archive);
    toast("Imported as '" + p.name + "'", "ok");
    location.hash = "#/p/" + p.id;
  }));
  return h("span", {}, file, h("button", { class: "btn", title: "Create a project from a full archive", onclick: () => file.click() }, icon("upload"), "Import"));
}

export async function newProjectDialog() {
  const name = h("input", { type: "text", maxlength: "120", placeholder: "e.g. CRM order capture" });
  const platform = select(Object.entries(state.meta.platforms), "salesforce");
  const desc = h("textarea", { rows: 3, placeholder: "Scope, business unit, release (optional)" });
  const people = state.me.username === "anonymous" ? [] : await api.get("/api/people");  // choosing people needs named accounts
  const access = people.length ? accessPicker(people) : null;
  modal({
    title: "New project", description: "The target platform shapes story vocabulary (objects, modules, integrations).",
    body: h("div", { class: "stack" }, field("Project name", name), field("Target platform", platform), field("Description", desc),
      access ? field("Who can see it", access.el) : null),
    actions: [{ label: "Create project", kind: "btn-primary", onClick: async () => {
      requireValue(name, "Project name is required");
      const chosen = access ? access.value() : { visibility: "everyone", members: [] };
      const p = await api.post("/api/projects", { name: name.value, platform: platform.value, description: desc.value, visibility: chosen.visibility });
      if (chosen.visibility === "members") await api.put("/api/projects/" + p.id + "/access", chosen);
      location.hash = "#/p/" + p.id + "/sources";
    } }],
  });
}

// With SSO, an expired or idle session means signing in again and coming back to the same page.
let signingIn = false;
setUnauthorizedHandler(() => {
  if (signingIn || !state.me || !state.me.sso) return;
  signingIn = true;
  location.assign("/auth/login?next=" + encodeURIComponent(location.pathname + location.hash));
});

// Tables are re-rendered by filters and the window can be resized: keep scrollable regions keyboard-reachable.
let scrollCheck = 0;
const scheduleScrollCheck = () => { cancelAnimationFrame(scrollCheck); scrollCheck = requestAnimationFrame(() => markScrollRegions()); };
new MutationObserver(scheduleScrollCheck).observe(document.body, { childList: true, subtree: true });
window.addEventListener("resize", scheduleScrollCheck);

// Leaving a page with unsaved edits: put the address back, ask, and only then go on to where the person was heading.
let shown = location.hash, reverting = false;
window.addEventListener("hashchange", async () => {
  if (reverting) { reverting = false; return; }
  const target = location.hash;
  if (target !== shown && hasPendingEdits()) {
    reverting = true;
    location.hash = shown;
    if (await confirmBox("What you changed on this page has not been saved.", { okLabel: "Discard", title: "Leave without saving?" })) {
      guardLeaving(null);
      location.hash = target;
    }
    return;
  }
  shown = target;
  route();
});
route();
