// App shell, routing and the projects home view.
import { h, mount, icon, api, guard, modal, field, requireValue, select, empty, badge, timeAgo, meter, setUnauthorizedHandler } from "./core.js";
import { renderProject } from "./project.js";
import { renderAdmin } from "./admin.js";
import { renderAccount } from "./account.js";

export const state = { meta: null, projects: [], me: null };

function navItem(href, label, active, iconName) {
  return h("a", { class: "nav-item" + (active ? " active" : ""), href, "aria-current": active ? "page" : null }, iconName ? icon(iconName) : null, label);
}

function globalSearch() {
  const listId = "project-search";
  const input = h("input", { type: "search", list: listId, placeholder: "Search projects…", "aria-label": "Search projects" });
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const q = input.value.trim().toLowerCase();
    const hit = state.projects.find((p) => p.name.toLowerCase() === q) || state.projects.find((p) => p.name.toLowerCase().includes(q));
    if (hit) location.hash = "#/p/" + hit.id;
  });
  return h("div", { class: "global-search" }, icon("search"), input, h("datalist", { id: listId }, state.projects.map((p) => h("option", { value: p.name }))));
}

function shell(route) {
  const isAdmin = state.me.role === "admin";
  const current = route[0] === "p" ? state.projects.find((p) => p.id === route[1]) : null;
  const header = h("header", { class: "global-header" },
    h("a", { class: "app-logo", href: "#/" }, h("span", { class: "brand-mark" }, "F"), "Fabulist"),
    globalSearch(),
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
  document.getElementById("app").replaceChildren(h("div", { class: "shell" }, skip, header, nav, h("main", { class: "main" }, content)));
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
async function route() {
  const ticket = ++rendering;
  const hash = location.hash, refocus = focusKey();
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  // Delayed so fast navigations don't flash; slow ones show the old page is being replaced.
  const busy = setTimeout(() => setBusy(true), 150);
  try {
    if (!state.meta) [state.meta, state.me] = await Promise.all([api.get("/api/meta"), api.get("/api/me")]);
    await refreshProjects();
    if (ticket !== rendering) return;
    const content = shell(parts);
    if (parts[0] === "p" && parts[1]) await renderProject(content, parts[1], parts[2] || "overview");
    else if (parts[0] === "admin" && state.me.role === "admin") await renderAdmin(content, parts[1] || "overview");
    else if (parts[0] === "account") await renderAccount(content);
    else renderHome(content);
    if (refocus && ticket === rendering && location.hash === hash && (!document.activeElement || document.activeElement === document.body)) {
      document.querySelector("#app " + refocus)?.focus();
    }
  } catch (e) {
    if (ticket !== rendering) return;
    const error = h("div", { class: "callout error", role: "alert" }, h("span", { class: "grow" }, e.message), h("a", { href: "#/" }, "Back to projects"));
    // Keep the header and navigation when we can, so a stale link isn't a dead end.
    if (state.me) mount(shell(parts), error);
    else document.getElementById("app").replaceChildren(h("div", { class: "content" }, error));
  } finally {
    clearTimeout(busy);
    if (ticket === rendering) setBusy(false);
  }
}

let archivedList = null;  // null: showing active projects

function renderHome(content, keepView = false) {
  setCrumbs("Projects");
  if (!keepView) archivedList = null;
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
    h("div", { class: "row" }, toggle, h("button", { class: "btn btn-primary", onclick: newProjectDialog }, "New")));
  if (!list.length) {
    mount(content, head, h("div", { class: "card" }, archivedList
      ? empty({ iconName: "folder", title: "No archived projects", text: "Archived projects are kept read-only, with their audit trail, until an administrator deletes them." })
      : empty({
        iconName: "folder", title: "No projects yet",
        text: "Create a project for a business application (e.g. CRM order capture), then upload its BRDs.",
        action: h("button", { class: "btn btn-primary", onclick: newProjectDialog }, icon("plus"), "Create project") })));
    return;
  }
  const cards = list.map((p) => {
    const pct = p.requirements ? Math.round((100 * p.requirements_approved) / p.requirements) : 0;
    return h("a", { class: "card project-card", href: "#/p/" + p.id },
      h("div", { class: "row" }, h("h3", { class: "grow" }, p.name), badge(state.meta.platforms[p.platform] || p.platform, "accent")),
      p.description ? h("p", { class: "muted small" }, p.description) : null,
      h("div", { class: "kv" },
        h("div", {}, h("b", {}, p.documents), h("span", {}, "BRDs")),
        h("div", {}, h("b", {}, p.requirements), h("span", {}, "Requirements")),
        h("div", {}, h("b", {}, p.stories), h("span", {}, "Stories"))),
      h("div", {}, h("div", { class: "row small muted" }, h("span", { class: "grow" }, "Requirements reviewed"), pct + "%"), meter(pct, "good")),
      h("div", { class: "muted small" }, "Created " + timeAgo(p.created)));
  });
  mount(content, head, h("div", { class: "grid grid-3" }, cards));
}

export function newProjectDialog() {
  const name = h("input", { type: "text", maxlength: "120", placeholder: "e.g. CRM order capture" });
  const platform = select(Object.entries(state.meta.platforms), "salesforce");
  const desc = h("textarea", { rows: 3, placeholder: "Scope, business unit, release (optional)" });
  modal({
    title: "New project", description: "The target platform shapes story vocabulary (objects, modules, integrations).",
    body: h("div", { class: "stack" }, field("Project name", name), field("Target platform", platform), field("Description", desc)),
    actions: [{ label: "Create project", kind: "btn-primary", onClick: async () => {
      requireValue(name, "Project name is required");
      const p = await api.post("/api/projects", { name: name.value, platform: platform.value, description: desc.value });
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

window.addEventListener("hashchange", route);
route();
