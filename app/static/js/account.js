// Account page: personal API tokens for automation (the secret is shown once).
import { h, mount, icon, api, guard, toast, modal, confirmBox, badge, timeAgo, field, select, segmented, empty } from "./core.js";
import { state, setCrumbs } from "./main.js";

const rerender = () => window.dispatchEvent(new HashChangeEvent("hashchange"));
export const when = (iso) => new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });

function revealToken(t) {
  const box = h("input", { type: "text", readonly: true, value: t.token, "aria-label": "New API token", style: { fontFamily: "var(--mono, monospace)" } });
  modal({
    title: "Token created",
    description: "Copy it now. For your security it will not be shown again.",
    body: h("div", { class: "stack" }, box,
      h("button", { class: "btn", onclick: guard(async () => {
        // navigator.clipboard needs HTTPS or localhost; plain-HTTP intranet installs fall back to the legacy command.
        if (navigator.clipboard) await navigator.clipboard.writeText(t.token);
        else { box.select(); if (!document.execCommand("copy")) throw new Error("Copy failed: select the token and copy it manually"); }
        toast("Token copied", "ok");
      }) }, icon("copy"), "Copy token"),
      h("p", { class: "muted small" }, "Send it as ", h("code", {}, "Authorization: Bearer <token>"), ". It expires on " + when(t.expires) + ".")),
  });
  setTimeout(() => { box.focus(); box.select(); }, 40);
}

function newTokenDialog() {
  const name = h("input", { type: "text", maxlength: "80", placeholder: "e.g. Nightly Jira sync" });
  const days = select([["30", "30 days"], ["90", "90 days"], ["180", "180 days"], ["365", "1 year"]], "90");
  modal({
    title: "New API token",
    description: "The token acts with your role (" + state.me.role + ") and stops working when it expires or you revoke it.",
    body: h("div", { class: "stack" }, field("Name", name, "What will use this token?"), field("Lifetime", days)),
    actions: [{ label: "Create token", kind: "btn-primary", onClick: async () => {
      if (!name.value.trim()) { toast("Give the token a name", "error"); return false; }
      const t = await api.post("/api/me/tokens", { name: name.value.trim(), days: Number(days.value) });
      rerender();
      setTimeout(() => revealToken(t), 60);
    } }],
  });
}

const THEME_KEY = "fabulist-theme";

// Light, dark or follow the system. The saved choice is applied before paint by theme.js; this changes it live.
function appearanceCard() {
  const saved = () => { try { return localStorage.getItem(THEME_KEY) || ""; } catch (_) { return ""; } };
  const apply = (v) => {
    try { v ? localStorage.setItem(THEME_KEY, v) : localStorage.removeItem(THEME_KEY); } catch (_) { /* storage blocked: applies until reload */ }
    v ? document.documentElement.setAttribute("data-theme", v) : document.documentElement.removeAttribute("data-theme");
  };
  return h("div", { class: "card", style: { marginTop: "16px" } }, h("div", { class: "card-head" }, h("h3", {}, "Appearance")),
    h("div", { class: "card-body" }, field("Colour theme", segmented([["", "System"], ["light", "Light"], ["dark", "Dark"]], saved(), apply),
      "System follows your device's light or dark setting. The choice is remembered in this browser.")));
}

// Named accounts that sign in with a password can change it here (SSO accounts have none, and tokens must not be able to).
function passwordCard() {
  const field_ = (label, name, auto) => field(label, h("input", { type: "password", name, autocomplete: auto, "aria-label": label }));
  const current = field_("Current password", "current", "current-password");
  const next = field_("New password", "new", "new-password");
  const again = field_("New password again", "again", "new-password");
  const val = (box) => box.querySelector("input").value;
  return h("div", { class: "card", style: { marginTop: "16px" } }, h("div", { class: "card-head" }, h("h3", {}, "Password")),
    h("div", { class: "card-body stack" }, current, next, again,
      h("div", { class: "muted small" }, "At least 12 characters. Your browser will ask you to sign in again with the new password."),
      h("div", {}, h("button", { class: "btn btn-primary", onclick: guard(async () => {
        if (val(next) !== val(again)) throw new Error("The new password and its repetition are different");
        await api.post("/api/me/password", { current_password: val(current), new_password: val(next) });
        toast("Password changed. Sign in again with the new one when your browser asks.", "ok");
        [current, next, again].forEach((b) => { b.querySelector("input").value = ""; });
      }) }, "Change password"))));
}

export async function renderAccount(content) {
  setCrumbs("Account");
  let tokens = null;
  if (state.me.named) {  // only an account with a row can own tokens: the bootstrap administrator and anonymous cannot, and asking would only log a 400
    try { tokens = await api.get("/api/me/tokens"); } catch (e) {
      if (!/named account/.test(e.message)) throw e;  // the bootstrap admin has no account row: show the explanation below
    }
  }
  const head = h("div", { class: "page-head" }, h("span", { class: "object-icon setup" }, icon("settings")),
    h("div", { class: "grow" }, h("div", { class: "object-label" }, "Account"), h("h1", {}, state.me.username),
      h("p", { class: "sub" }, "Role: " + state.me.role + ". API tokens let scripts and integrations act as you without sharing your password. ",
        h("a", { href: "/docs", target: "_blank", rel: "noopener" }, "API reference"))),
    tokens ? h("button", { class: "btn btn-primary", onclick: newTokenDialog }, icon("plus"), "New token") : null);
  if (!tokens) {
    mount(content, head, h("div", { class: "card" }, empty({ iconName: "lock", title: "Sign in with a named account",
      text: "API tokens belong to a named account. This deployment is not signed in, or you are using the bootstrap administrator.",
      action: state.me.role === "admin" && state.me.username !== "anonymous"
        ? h("a", { class: "btn btn-primary", href: "#/admin/users" }, icon("plus"), "Create a named account") : null })), appearanceCard());
    return;
  }
  mount(content, head, h("div", { class: "card" }, tokens.length
    ? h("div", { class: "table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, [["Name", ""], ["Token", ""], ["Created", "hide-sm"], ["Expires", ""], ["Last used", ""], ["", ""]].map(([x, c]) => h("th", { class: c }, x)))),
      h("tbody", {}, tokens.map((t) => h("tr", {},
        h("td", {}, t.name),
        h("td", { class: "mono small hide-sm" }, t.prefix + "…"),
        h("td", { class: "muted small nowrap hide-sm" }, when(t.created)),
        h("td", { class: "small nowrap" }, when(t.expires), new Date(t.expires) < new Date() ? [" ", badge("Expired", "red")] : null),
        h("td", { class: "muted small nowrap" }, t.last_used ? timeAgo(t.last_used) : "never"),
        h("td", {}, h("button", { class: "btn btn-sm btn-ghost btn-danger", "aria-label": "Revoke token " + t.name, onclick: guard(async () => {
          if (!(await confirmBox("Revoke '" + t.name + "'? Anything using it stops working immediately.", { danger: true, okLabel: "Revoke token" }))) return;
          await api.del("/api/me/tokens/" + t.id); toast("Token revoked", "ok"); rerender();
        }) }, "Revoke")))))))
    : empty({ iconName: "lock", title: "No API tokens", text: "Create one for a script or integration that needs to call the Fabulist API.",
      action: h("button", { class: "btn btn-primary", onclick: newTokenDialog }, icon("plus"), "New token") })), state.me.sso ? null : passwordCard(), appearanceCard());
}
