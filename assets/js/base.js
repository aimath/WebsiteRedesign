(() => {
  "use strict";

  const getStoredTheme = () => localStorage.getItem("theme");
  const setStoredTheme = (theme) => localStorage.setItem("theme", theme);

  const getPreferredTheme = () => {
    const storedTheme = getStoredTheme();
    if (storedTheme) {
      return storedTheme;
    }

    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  };

  const setTheme = (theme) => {
    if (theme === "auto") {
      document.documentElement.setAttribute(
        "data-bs-theme",
        window.matchMedia("(prefers-color-scheme: dark)").matches
          ? "dark"
          : "light"
      );
    } else {
      document.documentElement.setAttribute("data-bs-theme", theme);
    }
  };

  setTheme(getPreferredTheme());

  const showActiveTheme = (theme, focus = false) => {
    const themeSwitcher = document.querySelector("#bd-theme");

    if (!themeSwitcher) {
      return;
    }

    const themeSwitcherText = document.querySelector("#bd-theme-text");
    const activeThemeIcon = document.querySelector(".theme-icon-active use");
    const btnToActive = document.querySelector(
      `[data-bs-theme-value="${theme}"]`
    );
    const svgOfActiveBtn = btnToActive
      .querySelector("svg use")
      .getAttribute("href");

    document.querySelectorAll("[data-bs-theme-value]").forEach((element) => {
      element.classList.remove("active");
      element.setAttribute("aria-pressed", "false");
    });

    btnToActive.classList.add("active");
    btnToActive.setAttribute("aria-pressed", "true");
    activeThemeIcon.setAttribute("href", svgOfActiveBtn);
    const themeSwitcherLabel = `${themeSwitcherText.textContent} (${btnToActive.dataset.bsThemeValue})`;
    themeSwitcher.setAttribute("aria-label", themeSwitcherLabel);

    if (focus) {
      themeSwitcher.focus();
    }
  };

  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", () => {
      const storedTheme = getStoredTheme();
      if (storedTheme !== "light" && storedTheme !== "dark") {
        setTheme(getPreferredTheme());
      }
    });

  window.addEventListener("DOMContentLoaded", () => {
    showActiveTheme(getPreferredTheme());

    document.querySelectorAll("[data-bs-theme-value]").forEach((toggle) => {
      toggle.addEventListener("click", () => {
        const theme = toggle.getAttribute("data-bs-theme-value");
        setStoredTheme(theme);
        setTheme(theme);
        showActiveTheme(theme, true);
      });
    });
  });
})();

// Mobile nav: first tap expands dropdown, second tap navigates.
// Dropdowns have no data-bs-toggle so they navigate on desktop hover.
// On mobile we manually manage open/close.
(function () {
  function closeMobileDropdowns(except) {
    document.querySelectorAll(".nav-item.dropdown.mobile-open").forEach(function (item) {
      if (item !== except) {
        item.classList.remove("mobile-open");
        item.querySelector(".dropdown-menu").classList.remove("show");
        item.querySelector(".nav-link").setAttribute("aria-expanded", "false");
      }
    });
  }

  document.addEventListener("click", function (e) {
    if (window.innerWidth > 800) return;

    const link = e.target.closest(".nav-item.dropdown > .nav-link");
    if (link) {
      const item = link.parentElement;
      const menu = item.querySelector(".dropdown-menu");

      if (!item.classList.contains("mobile-open")) {
        // First tap: expand, prevent navigation
        e.preventDefault();
        closeMobileDropdowns(item);
        item.classList.add("mobile-open");
        menu.classList.add("show");
        link.setAttribute("aria-expanded", "true");
      } else {
        // Second tap: let navigation happen, close menu
        item.classList.remove("mobile-open");
        menu.classList.remove("show");
        link.setAttribute("aria-expanded", "false");
      }
      return;
    }

    // Tap outside any dropdown — close all
    if (!e.target.closest(".nav-item.dropdown")) {
      closeMobileDropdowns(null);
    }
  });
})();

const prev = document.getElementById("prev-btn");
const next = document.getElementById("next-btn");
const list = document.getElementById("horizontal-container");
const itemWidth = 300;
const padding = 10;

if (prev !== null) {
  prev.addEventListener("click", () => {
    list.scrollLeft -= itemWidth + padding;
  });

  next.addEventListener("click", () => {
    list.scrollLeft += itemWidth + padding;
  });
}
