(function () {
  "use strict";

  const btn = document.getElementById("projectSelectorBtn");
  const menu = document.getElementById("projectSelectorMenu");

  if (!btn || !menu) return;

  function open() {
    menu.hidden = false;
    btn.setAttribute("aria-expanded", "true");
  }

  function close() {
    menu.hidden = true;
    btn.setAttribute("aria-expanded", "false");
  }

  btn.addEventListener("click", function (e) {
    e.stopPropagation();
    menu.hidden ? open() : close();
  });

  document.addEventListener("click", function (e) {
    if (!menu.hidden && !menu.contains(e.target)) {
      close();
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !menu.hidden) {
      close();
      btn.focus();
    }
  });

  // Auto-generate slug from name on new-project form
  const nameInput = document.getElementById("name");
  const slugInput = document.getElementById("slug");
  if (nameInput && slugInput && !slugInput.value) {
    let slugEdited = false;
    slugInput.addEventListener("input", function () {
      slugEdited = true;
    });
    nameInput.addEventListener("input", function () {
      if (!slugEdited) {
        slugInput.value = nameInput.value
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-+|-+$/g, "");
      }
    });
  }
})();
