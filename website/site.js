(() => {
  "use strict";

  const menuToggle = document.querySelector("[data-menu-toggle]");
  const siteNav = document.getElementById("site-nav");

  if (menuToggle && siteNav) {
    const setMenuOpen = (open, returnFocus = false) => {
      menuToggle.setAttribute("aria-expanded", String(open));
      menuToggle.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
      siteNav.classList.toggle("is-open", open);
      if (returnFocus) menuToggle.focus();
    };

    menuToggle.addEventListener("click", () => {
      setMenuOpen(menuToggle.getAttribute("aria-expanded") !== "true");
    });

    siteNav.addEventListener("click", (event) => {
      if (event.target.closest("a[href^='#']")) setMenuOpen(false);
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && menuToggle.getAttribute("aria-expanded") === "true") {
        setMenuOpen(false, true);
      }
    });

    // The navigation is always visible at desktop widths; clear the mobile state
    // when crossing into that layout so it cannot reopen unexpectedly on return.
    const desktopLayout = window.matchMedia("(min-width: 761px)");
    const syncMenuToLayout = () => {
      if (desktopLayout.matches) setMenuOpen(false);
    };
    if (desktopLayout.addEventListener) {
      desktopLayout.addEventListener("change", syncMenuToLayout);
    } else {
      desktopLayout.addListener(syncMenuToLayout);
    }
  }

  const installMenus = Array.from(document.querySelectorAll("[data-install-menu]"));
  if (installMenus.length) {
    document.addEventListener("click", (event) => {
      installMenus.forEach((menu) => {
        if (menu.open && !menu.contains(event.target)) menu.open = false;
      });
    });

    installMenus.forEach((menu) => {
      menu.addEventListener("toggle", () => {
        if (menu.open) {
          window.AgentTelemetryAnalytics?.track("install_menu_open", { placement: "hero" });
        }
      });

      menu.addEventListener("click", (event) => {
        if (event.target.closest("a[href]")) menu.open = false;
      });

      // The top layer lets frost sample the actual page behind the chooser,
      // beyond the hero's animated stacking contexts. Native details is the fallback.
      const panel = menu.querySelector(".install-popover");
      const trigger = menu.querySelector("summary");
      if (!panel || !trigger || typeof panel.showPopover !== "function") return;
      panel.setAttribute("popover", "manual");
      let placementFrame = 0;
      const placePanel = () => {
        placementFrame = 0;
        if (!panel.matches(":popover-open")) return;
        const anchor = trigger.getBoundingClientRect();
        if (anchor.bottom < 0 || anchor.top > window.innerHeight) {
          menu.open = false;
          panel.hidePopover();
          return;
        }
        const gap = 12, edge = 12;
        const width = Math.min(window.innerWidth <= 480 ? anchor.width : 420, window.innerWidth - edge * 2);
        panel.style.setProperty("--install-width", `${width}px`);
        const height = panel.offsetHeight;
        const below = window.innerHeight - anchor.bottom - gap - edge;
        const above = anchor.top - gap - edge;
        const openAbove = below < height && above >= height;
        const top = openAbove ? anchor.top - gap - height : Math.min(anchor.bottom + gap, window.innerHeight - height - edge);
        panel.style.setProperty("--install-left", `${Math.max(edge, Math.min(anchor.left, window.innerWidth - width - edge))}px`);
        panel.style.setProperty("--install-top", `${Math.max(edge, top)}px`);
        panel.dataset.placement = openAbove ? "above" : "below";
      };
      const requestPlacement = () => {
        if (menu.open && !placementFrame) placementFrame = window.requestAnimationFrame(placePanel);
      };
      menu.addEventListener("toggle", () => {
        if (menu.open) {
          panel.showPopover({ source: trigger });
          placePanel();
        } else if (panel.matches(":popover-open")) {
          panel.hidePopover();
        }
      });
      window.addEventListener("resize", requestPlacement, { passive: true });
      window.addEventListener("scroll", requestPlacement, { passive: true });
    });

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      const openMenus = installMenus.filter((menu) => menu.open);
      if (!openMenus.length) return;

      const focusedMenu = openMenus.find((menu) => menu.contains(document.activeElement));
      openMenus.forEach((menu) => { menu.open = false; });
      if (!focusedMenu) return;

      event.preventDefault();
      event.stopImmediatePropagation();
      focusedMenu.querySelector("summary")?.focus();
    }, { capture: true });
  }

  const showcase = document.querySelector(".dashboard-showcase");
  const tabButtons = Array.from(document.querySelectorAll("[data-demo-tab]"));
  const sidebarButtons = Array.from(document.querySelectorAll("[data-demo-view]"));
  const tabPanels = Array.from(document.querySelectorAll("[data-demo-panel]"));

  if (showcase && tabPanels.length) {
    const panelById = new Map(tabPanels.map((panel) => [panel.id, panel]));
    const panelByView = new Map(tabPanels.map((panel) => [panel.dataset.demoPanel, panel]));
    const viewSelect = showcase.querySelector("[data-demo-select]");
    const viewForControl = (button) => {
      const explicitView = button.dataset.demoView || button.dataset.demoTab;
      if (explicitView && panelByView.has(explicitView)) return explicitView;
      const panel = panelById.get(button.getAttribute("aria-controls"));
      return panel ? panel.dataset.demoPanel : null;
    };
    const enabledTabs = tabButtons.filter((button) => viewForControl(button));
    const enabledSidebarButtons = sidebarButtons.filter((button) => viewForControl(button));
    const viewNames = Array.from(panelByView.keys()).filter(Boolean);

    const initialSidebarView = enabledSidebarButtons.find((button) => button.getAttribute("aria-pressed") === "true" || button.classList.contains("active"));
    const initialTab = enabledTabs.find((button) => button.getAttribute("aria-selected") === "true");
    const initialView = (initialSidebarView && viewForControl(initialSidebarView)) ||
      (initialTab && viewForControl(initialTab)) ||
      (panelByView.has("overview") ? "overview" : viewNames[0]);
    let rovingTabView = (initialTab && viewForControl(initialTab)) ||
      (panelByView.has("overview") ? "overview" : viewNames[0]);

    const setActiveView = (view, options = {}) => {
      if (!panelByView.has(view)) return false;
      if (enabledTabs.some((button) => viewForControl(button) === view)) rovingTabView = view;

      tabPanels.forEach((panel) => {
        const selected = panel.dataset.demoPanel === view;
        panel.hidden = !selected;
        panel.tabIndex = selected ? 0 : -1;
      });

      enabledTabs.forEach((button) => {
        const selected = viewForControl(button) === view;
        button.setAttribute("aria-selected", String(selected));
        button.tabIndex = viewForControl(button) === rovingTabView ? 0 : -1;
      });

      enabledSidebarButtons.forEach((button) => {
        const selected = viewForControl(button) === view;
        button.classList.toggle("active", selected);
        button.setAttribute("aria-pressed", String(selected));
      });
      if (viewSelect) viewSelect.value = view;

      if (options.moveFocus && options.focusTarget) options.focusTarget.focus();
      if (options.announce !== false) {
        showcase.dispatchEvent(new CustomEvent("demochange", {
          bubbles: true,
          detail: { view }
        }));
      }
      return true;
    };

    enabledTabs.forEach((button) => {
      button.addEventListener("click", () => setActiveView(viewForControl(button)));
      button.addEventListener("keydown", (event) => {
        const index = enabledTabs.indexOf(button);
        let nextIndex;
        switch (event.key) {
          case "ArrowRight":
          case "ArrowDown":
            nextIndex = (index + 1) % enabledTabs.length;
            break;
          case "ArrowLeft":
          case "ArrowUp":
            nextIndex = (index - 1 + enabledTabs.length) % enabledTabs.length;
            break;
          case "Home":
            nextIndex = 0;
            break;
          case "End":
            nextIndex = enabledTabs.length - 1;
            break;
          default:
            return;
        }
        event.preventDefault();
        const nextButton = enabledTabs[nextIndex];
        setActiveView(viewForControl(nextButton), { moveFocus: true, focusTarget: nextButton });
      });
    });

    enabledSidebarButtons.forEach((button) => {
      button.addEventListener("click", () => setActiveView(viewForControl(button)));
    });

    viewSelect?.addEventListener("change", () => setActiveView(viewSelect.value));

    setActiveView(initialView);

    const windowControls = Array.from(showcase.querySelectorAll("[data-demo-window]"));
    const controlFor = (action) => windowControls.find((button) => button.dataset.demoWindow === action);
    const minimizeButton = controlFor("minimize");
    const restoreButton = controlFor("restore");
    const expandButton = controlFor("expand");
    const resetButton = controlFor("reset");
    const showcaseBody = showcase.querySelector(".showcase-body");

    const syncWindowControls = () => {
      const minimized = showcase.classList.contains("is-minimized");
      const expanded = showcase.classList.contains("is-expanded");
      if (showcaseBody) showcaseBody.hidden = minimized;
      if (minimizeButton) {
        minimizeButton.hidden = minimized;
        minimizeButton.setAttribute("aria-expanded", String(!minimized));
      }
      if (restoreButton) {
        restoreButton.hidden = !minimized;
        restoreButton.setAttribute("aria-expanded", String(!minimized));
      }
      if (expandButton) expandButton.setAttribute("aria-pressed", String(expanded));
    };

    minimizeButton?.addEventListener("click", () => {
      showcase.classList.add("is-minimized");
      syncWindowControls();
      restoreButton?.focus();
    });

    restoreButton?.addEventListener("click", () => {
      showcase.classList.remove("is-minimized");
      syncWindowControls();
      minimizeButton?.focus();
    });

    expandButton?.addEventListener("click", () => {
      showcase.classList.remove("is-minimized");
      showcase.classList.toggle("is-expanded");
      syncWindowControls();
    });

    resetButton?.addEventListener("click", () => {
      showcase.classList.remove("is-minimized", "is-expanded");
      syncWindowControls();
      setActiveView("overview");
    });

    syncWindowControls();

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !showcase.classList.contains("is-expanded")) return;
      const focusWasInsideShowcase = showcase.contains(document.activeElement);
      showcase.classList.remove("is-expanded");
      syncWindowControls();
      if (focusWasInsideShowcase) expandButton?.focus();
    });
  }

  const copyButtons = Array.from(document.querySelectorAll("[data-copy-command]"));
  copyButtons.forEach((button) => {
    const codeWrap = button.closest(".code-wrap");
    const commandTargetId = button.getAttribute("data-command-target");
    const statusId = button.getAttribute("data-copy-status");
    const commandElement = (commandTargetId && document.getElementById(commandTargetId.replace(/^#/, ""))) ||
      codeWrap?.querySelector("pre code") ||
      (!commandTargetId ? document.getElementById("install-command") : null);
    const statusElement = (statusId && document.getElementById(statusId.replace(/^#/, ""))) ||
      codeWrap?.querySelector(".copy-status, [role='status']") ||
      (!statusId ? document.getElementById("copy-status") : null);

    const announce = (message) => {
      if (statusElement) statusElement.textContent = message;
    };

    const manualCopy = () => {
      const selection = window.getSelection();
      if (selection && commandElement) {
        const range = document.createRange();
        range.selectNodeContents(commandElement);
        selection.removeAllRanges();
        selection.addRange(range);
      }
      announce("Select the highlighted command and copy it manually.");
    };

    button.addEventListener("click", async () => {
      const command = commandElement?.textContent.trim();
      if (!command) {
        announce("The command is unavailable.");
        return;
      }
      window.AgentTelemetryAnalytics?.track("install_command_copy", {
        install_option: button.dataset.analyticsOption,
        install_method: button.dataset.analyticsMethod,
        placement: button.dataset.analyticsPlacement,
      });

      if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") {
        manualCopy();
        return;
      }

      try {
        await navigator.clipboard.writeText(command);
        announce("Command copied to clipboard.");
      } catch (_error) {
        manualCopy();
      }
    });
  });

  // Control enhancement and motion have separate readiness states. If motion.js
  // is unavailable, content remains visible and the controls still work.
  document.documentElement.classList.add("is-ready");
})();
