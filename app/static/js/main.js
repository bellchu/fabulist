// App shell, routing and the projects home view.
import { h, mount, icon, api, guard, modal, field, select, empty, badge, timeAgo, meter } from "./core.js";
import { renderProject } from "./project.js";
import { renderAdmin } from "./admin.js";

export const state = { meta: null, projects: [] };

function navItem(href, iconName, label, active) {
  return h("a", { class: "nav-item" + (active ? " active" : ""), href }, icon(iconName), label);
}

function shell(route) {
  const sidebar = h("nav", { class: "sidebar", "aria-label": "Main" },
    h("a", { class: "brand", href: "#/" }, h("span", { class: "brand-mark" }, "F"), "Fabulist"),
    h("div", { class: "nav-label" }, "Workspace"),
    navItem("#/", "folder", "Projects", route[0] === "" || route[0] === "p"),
    ...state.projects.slice(0, 6).map((p) =>
      h("a", { class: "nav-item nav-sub" + (route[0] === "p" && route[1] === p.id ? " active" : ""), href: "#/p/" + p.id }, p.name)),
    h("div", { class: "nav-label" }, "Administration"),
    navItem("#/admin/overview", "chart", "Overview", route[0] === "admin" && (route[1] || "overview") === "overview"),
    navItem("#/admin/providers", "cpu", "AI providers", route[0] === "admin" && route[1] === "providers"),
    navItem("#/admin/settings", "settings", "Generation settings", route[0] === "admin" && route[1] === "settings"),
    navItem("#/admin/integrations", "link", "Integrations", route[0] === "admin" && route[1] === "integrations"),
    navItem("#/admin/activity", "activity", "Activity", route[0] === "admin" && route[1] === "activity"),
    h("div", { class: "sidebar-foot" }, "BRD → requirements → stories"));
  const topbar = h("header", { class: "topbar" },
    h("div", { class: "mobile-nav" }, h("a", { class: "btn btn-sm", href: "#/" }, "Projects"), h("a", { class: "btn btn-sm", href: "#/admin/overview" }, "Admin")),
    h("div", { class: "crumbs", id: "crumbs" }));
  const content = h("div", { class: "content", id: "content" });
  document.getElementById("app").replaceChildren(h("div", { class: "shell" }, sidebar, h("main", { class: "main" }, topbar, content)));
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

let rendering = 0;
async function route() {
  const ticket = ++rendering;
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  try {
    if (!state.meta) state.meta = await api.get("/api/meta");
    await refreshProjects();
    if (ticket !== rendering) return;
    const content = shell(parts);
    if (parts[0] === "p" && parts[1]) await renderProject(content, parts[1], parts[2] || "overview");
    else if (parts[0] === "admin") await renderAdmin(content, parts[1] || "overview");
    else renderHome(content);
  } catch (e) {
    document.getElementById("app").replaceChildren(h("div", { class: "content" }, h("div", { class: "callout error" }, e.message)));
  }
}

function renderHome(content) {
  setCrumbs("Projects");
  const head = h("div", { class: "page-head" },
    h("div", { class: "grow" }, h("h1", {}, "Projects"),
      h("p", { class: "sub" }, "Each project turns a set of BRDs into reviewed requirements and a traceable backlog.")),
    h("button", { class: "btn btn-primary", onclick: newProjectDialog }, icon("plus"), "New project"));
  if (!state.projects.length) {
    mount(content, head, h("div", { class: "card" }, empty({
      iconName: "folder", title: "No projects yet",
      text: "Create a project for a business application (e.g. CRM order capture), then upload its BRDs.",
      action: h("button", { class: "btn btn-primary", onclick: newProjectDialog }, icon("plus"), "Create project") })));
    return;
  }
  const cards = state.projects.map((p) => {
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
      if (!name.value.trim()) throw new Error("Project name is required");
      const p = await api.post("/api/projects", { name: name.value, platform: platform.value, description: desc.value });
      location.hash = "#/p/" + p.id + "/sources";
    } }],
  });
}

window.addEventListener("hashchange", route);
route();
