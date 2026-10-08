// The release dropdowns on the prompt rules pages. Every release of a category is in
// the page, written by docs/_hooks/rules_catalog.py; this shows the one chosen.
function showRulesReleases() {
  for (const select of document.querySelectorAll(".rules-release__select")) {
    const views = document.querySelectorAll(
      `.rules-release__view[data-category="${select.dataset.category}"]`,
    );
    const show = () => {
      for (const view of views) view.hidden = view.dataset.release !== select.value;
    };
    select.addEventListener("change", show);
    show(); // a browser that restores the choice on back navigation shows it too
  }
}

// Material re-renders the page body on instant navigation; document$ fires each time.
if (typeof document$ !== "undefined") {
  document$.subscribe(showRulesReleases);
} else {
  document.addEventListener("DOMContentLoaded", showRulesReleases);
}
