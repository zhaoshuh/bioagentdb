/* Path-based public routes over the original home page.
 *
 * The old home was a single long page with #diseases/#how/#ledger tabs.
 * We keep its markup and data loaders, but expose clean paths:
 *   /topics  /method  /records
 * On those paths only the matching section is shown; "/" keeps the full home.
 */
(function () {
  "use strict";
  const path = location.pathname.replace(/\/+$/, "") || "/";
  const map = {
    "/topics": { id: "diseases", label: "Topics" },
    "/method": { id: "how", label: "Method" },
    "/records": { id: "ledger", label: "Records" },
  };
  const route = map[path];
  if (!route) return;

  const sections = ["hero", "diseases", "how", "ledger"];
  sections.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.style.display = id === route.id ? "" : "none";
  });
  document.querySelectorAll(".navlinks a").forEach((a) => {
    const isRoute = (a.getAttribute("href") || "") === path;
    a.classList.toggle("active", isRoute);
    a.classList.toggle("route-active", isRoute);
    if (isRoute) a.setAttribute("aria-current", "page");
  });
  document.title = "BioAgentDB — " + route.label;
  window.scrollTo(0, 0);
})();
