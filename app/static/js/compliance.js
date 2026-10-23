// Every compliance tag across the projects the person may open: where it applies and how much of it a story covers.
import { h, mount, api, empty, icon, download } from "./core.js";
import { setCrumbs, setTitle } from "./main.js";

export async function renderCompliance(content) {
  setCrumbs("Compliance");
  setTitle("Compliance");
  const { tags } = await api.get("/api/compliance");
  const head = h("div", { class: "page-head" }, h("span", { class: "object-icon project" }, icon("shield")),
    h("div", { class: "grow" }, h("div", { class: "object-label" }, "Compliance"), h("h1", {}, "Compliance coverage"),
      h("p", { class: "sub" }, "Each compliance tag across every project you can open: how many requirements carry it and how many of those a story covers. Archived projects are left out."),
    ), tags.length ? h("button", { class: "btn", title: "One row per tag and project, for audit evidence", onclick: () => download("/api/compliance.csv") }, icon("download"), "Download CSV") : null);
  if (!tags.length) {
    mount(content, head, h("div", { class: "card" }, empty({ iconName: "shield", title: "No compliance tags yet", text: "Tag requirements (Tag… in a project's requirements list) and they are counted here." })));
    return;
  }
  mount(content, head, h("div", { class: "card table-wrap" }, h("table", { class: "table", "aria-label": "Compliance coverage by tag" },
    h("thead", {}, h("tr", {}, ["Tag", "Requirements", "Covered by stories", "Projects"].map((x) => h("th", { scope: "col" }, x)))),
    h("tbody", {}, tags.map((t) => h("tr", {},
      h("td", {}, h("a", { href: "#/search/" + encodeURIComponent(t.tag), title: "Everything tagged " + t.tag }, t.tag)),
      h("td", {}, String(t.requirements)),
      h("td", {}, t.covered + " of " + t.requirements + (t.covered < t.requirements ? " – gaps" : "")),
      h("td", { class: "small" }, h("span", { class: "row wrap" }, t.projects.map((p) =>
        h("a", { class: "chip", href: "#/p/" + p.id + "/overview", title: p.covered + " of " + p.requirements + " covered" }, p.name + " · " + p.requirements))))))))));
}
