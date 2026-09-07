(function () {
  "use strict";

  var themeKey = "mq-app-theme";

  function readTheme() {
    try {
      return window.localStorage.getItem(themeKey) === "dark" ? "dark" : "light";
    } catch (_error) {
      return "light";
    }
  }

  function applyTheme(theme) {
    var root = document.documentElement;
    if (theme === "dark") {
      root.setAttribute("data-mq-theme", "dark");
    } else {
      root.removeAttribute("data-mq-theme");
    }
    root.style.colorScheme = theme;
    try {
      window.localStorage.setItem(themeKey, theme);
    } catch (_error) {
      return;
    }
  }

  applyTheme(readTheme());

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-mq-theme-value]").forEach(function (button) {
      var selected = button.getAttribute("data-mq-theme-value") === readTheme();
      button.setAttribute("aria-pressed", selected ? "true" : "false");
      button.addEventListener("click", function () {
        var theme = button.getAttribute("data-mq-theme-value");
        applyTheme(theme);
        document.querySelectorAll("[data-mq-theme-value]").forEach(function (item) {
          item.setAttribute(
            "aria-pressed",
            item.getAttribute("data-mq-theme-value") === theme ? "true" : "false"
          );
        });
      });
    });

    var navigation = document.getElementById("primary-navigation");
    var toggle = document.querySelector("[data-mq-nav-toggle]");
    if (navigation && toggle) {
      toggle.addEventListener("click", function () {
        var collapsed = navigation.classList.toggle("mq-nav--collapsed");
        toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
        toggle.textContent = collapsed ? "»" : "«";
      });
    }

    var activeNavigationItem = document.querySelector(".mq-nav__item--active");
    if (activeNavigationItem && window.matchMedia("(max-width: 720px)").matches) {
      activeNavigationItem.scrollIntoView({ block: "nearest", inline: "center" });
    }
  });
})();
