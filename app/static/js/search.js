// Search across every project the person may see: projects by name, requirements and stories by their words or compliance tag.
import { h, mount, api, badge, empty, icon, statusTone } from "./core.js";
import { setCrumbs, setTitle } from "./main.js";

export async function renderSearch(content, q) {
  setCrumbs("Search");
  if (q.trim()) setTitle("“" + q.trim().slice(0, 60) + "”", "Search");  // the query in the tab title and the history entry
  const head = h("div", { class: "page-head" }, h("span", { class: "object-icon project" }, icon("search")),
    h("div", { class: "grow" }, h("div", { class: "object-label" }, "Search"), h("h1", {}, q ? "Results for “" + q + "”" : "Search"),
      h("p", { class: "sub" }, "Projects by name, requirements and stories by their words or compliance tag, across every project you can open. Archived projects are left out.")));
  if (q.trim().length < 2) {
    mount(content, head, h("div", { class: "card" }, empty({ iconName: "search", title: "Type at least 2 characters", text: "Use the search box at the top of the page." })));
    return;
  }
  const r = await api.get("/api/search?q=" + encodeURIComponent(q.trim()) + "&limit=50");
  const section = (title, rows, row) => rows.length ? h("div", { class: "card", style: { marginBottom: "16px" } },
    h("div", { class: "card-head" }, h("h3", {}, title + " (" + rows.length + (rows.length === 50 ? "+" : "") + ")")),
    h("div", { class: "table-wrap" }, h("table", { class: "table", "aria-label": title },
      h("tbody", {}, rows.map(row))))) : null;
  const where = (x, tab) => h("a", { href: "#/p/" + x.project_id + "/" + tab }, x.project_name);
  const none = !r.projects.length && !r.requirements.length && !r.stories.length;
  mount(content, head,
    none ? h("div", { class: "card" }, empty({ iconName: "search", title: "Nothing found", text: "Try fewer or different words. Projects you cannot open are not searched." })) : null,
    section("Projects", r.projects, (p) => h("tr", {}, h("td", {}, h("a", { href: "#/p/" + p.id }, p.name)), h("td", { class: "muted small" }, p.platform))),
    section("Requirements", r.requirements, (x) => h("tr", {},
      h("td", { class: "mono nowrap" }, h("a", { href: "#/p/" + x.project_id + "/requirements/" + x.key }, x.key)), h("td", { class: "small nowrap" }, where(x, "requirements")),
      h("td", {}, x.statement), h("td", {}, badge(x.status, statusTone[x.status])))),
    section("Stories", r.stories, (x) => h("tr", {},
      h("td", { class: "mono nowrap" }, h("a", { href: "#/p/" + x.project_id + "/stories/" + x.key }, x.key)), h("td", { class: "small nowrap" }, where(x, "stories")),
      h("td", {}, x.title), h("td", {}, badge(x.status, statusTone[x.status])))));
}
