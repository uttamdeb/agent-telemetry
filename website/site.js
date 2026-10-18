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

    document.addEventListener("click", (event) => {
      if (!siteNav.contains(event.target) && !menuToggle.contains(event.target)) setMenuOpen(false);
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
    const closeInstallMenu = (menu, returnFocus = false) => {
      menu.open = false;
      const panel = menu.querySelector(".install-popover");
      if (typeof panel?.hidePopover === "function" && panel.matches(":popover-open")) panel.hidePopover();
      if (returnFocus) menu.querySelector("summary")?.focus();
    };
    document.addEventListener("click", (event) => {
      installMenus.forEach((menu) => {
        if (menu.open && !menu.contains(event.target)) closeInstallMenu(menu);
      });
    });
    document.addEventListener("focusin", (event) => {
      installMenus.forEach((menu) => {
        if (menu.open && !menu.contains(event.target)) closeInstallMenu(menu);
      });
    });

    installMenus.forEach((menu) => {
      menu.addEventListener("click", (event) => {
        if (event.target.closest("a[href]")) closeInstallMenu(menu);
      });

      // The top layer lets frost sample the actual page behind the chooser,
      // beyond the hero's animated stacking contexts. Native details is the fallback.
      const panel = menu.querySelector(".install-popover");
      const trigger = menu.querySelector("summary");
      if (!panel || !trigger) return;
      let topLayer = typeof panel.showPopover === "function";
      if (topLayer) panel.setAttribute("popover", "manual");
      let placementFrame = 0;
      let focusOnOpen = null;
      const placePanel = () => {
        placementFrame = 0;
        if (!topLayer || !panel.matches(":popover-open")) return;
        const anchor = trigger.getBoundingClientRect();
        const viewport = window.visualViewport;
        const left = viewport?.offsetLeft || 0;
        const top = viewport?.offsetTop || 0;
        const width = viewport?.width || window.innerWidth;
        const height = viewport?.height || window.innerHeight;
        const right = left + width, bottom = top + height;
        if (anchor.bottom < top || anchor.top > bottom || anchor.right < left || anchor.left > right) {
          closeInstallMenu(menu);
          return;
        }
        const gap = 10, edge = 12;
        const panelWidth = Math.min(width <= 480 ? Math.max(anchor.width, 320) : 420, width - edge * 2);
        panel.style.setProperty("--install-width", `${panelWidth}px`);
        const style = getComputedStyle(panel);
        const border = (parseFloat(style.borderTopWidth) || 0) + (parseFloat(style.borderBottomWidth) || 0);
        const naturalHeight = panel.scrollHeight + border;
        const below = bottom - anchor.bottom - gap - edge;
        const above = anchor.top - top - gap - edge;
        const openAbove = below < naturalHeight && above > below;
        const maxHeight = Math.min(Math.max(96, openAbove ? above : below), Math.max(0, height - edge * 2));
        panel.style.setProperty("--install-max-height", `${maxHeight}px`);
        const panelHeight = panel.offsetHeight;
        const panelLeft = Math.max(left + edge, Math.min(anchor.left, right - panelWidth - edge));
        const panelTop = openAbove ? anchor.top - gap - panelHeight : anchor.bottom + gap;
        panel.style.setProperty("--install-left", `${panelLeft}px`);
        panel.style.setProperty("--install-top", `${Math.max(top + edge, Math.min(panelTop, bottom - panelHeight - edge))}px`);
        panel.style.setProperty("--popover-origin-x", `${Math.max(18, Math.min(anchor.left + anchor.width / 2 - panelLeft, panelWidth - 18))}px`);
        panel.dataset.placement = openAbove ? "above" : "below";
      };
      const requestPlacement = () => {
        if (menu.open && !placementFrame) placementFrame = window.requestAnimationFrame(placePanel);
      };
      menu.addEventListener("toggle", () => {
        trigger.setAttribute("aria-expanded", String(menu.open));
        if (menu.open) {
          installMenus.forEach((other) => { if (other !== menu && other.open) closeInstallMenu(other); });
          if (topLayer) {
            try {
              if (!panel.matches(":popover-open")) panel.showPopover();
              placePanel();
            } catch (_error) {
              // A partially supported Popover API must not hide the native chooser.
              topLayer = false;
              panel.removeAttribute("popover");
            }
          }
          if (focusOnOpen !== null) {
            const options = Array.from(panel.querySelectorAll("a[href]"));
            (focusOnOpen === "last" ? options[options.length - 1] : options[0])?.focus();
            focusOnOpen = null;
          }
        } else {
          focusOnOpen = null;
          if (topLayer && panel.matches(":popover-open")) panel.hidePopover();
        }
      });
      trigger.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
        event.preventDefault();
        const last = event.key === "ArrowUp";
        if (menu.open) {
          const options = Array.from(panel.querySelectorAll("a[href]"));
          (last ? options[options.length - 1] : options[0])?.focus();
        } else {
          focusOnOpen = last ? "last" : "first";
          menu.open = true;
        }
      });
      panel.addEventListener("keydown", (event) => {
        if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
        const options = Array.from(panel.querySelectorAll("a[href]"));
        const index = options.indexOf(document.activeElement);
        if (index < 0) return;
        event.preventDefault();
        const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 :
          (index + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
        options[next]?.focus();
      });
      window.addEventListener("resize", requestPlacement, { passive: true });
      window.addEventListener("scroll", requestPlacement, { passive: true });
      window.visualViewport?.addEventListener("resize", requestPlacement, { passive: true });
      window.visualViewport?.addEventListener("scroll", requestPlacement, { passive: true });
    });

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      const openMenus = installMenus.filter((menu) => menu.open);
      if (!openMenus.length) return;

      const focusedMenu = openMenus.find((menu) => menu.contains(document.activeElement));
      openMenus.forEach((menu) => closeInstallMenu(menu));
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
      button.addEventListener("keydown", (event) => {
        if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const index = enabledSidebarButtons.indexOf(button);
        const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? enabledSidebarButtons.length - 1 :
          (index + (event.key === "ArrowDown" ? 1 : -1) + enabledSidebarButtons.length) % enabledSidebarButtons.length;
        const nextButton = enabledSidebarButtons[nextIndex];
        setActiveView(viewForControl(nextButton), { moveFocus: true, focusTarget: nextButton });
      });
    });

    viewSelect?.addEventListener("change", () => setActiveView(viewSelect.value));

    document.querySelectorAll("[data-demo-go]").forEach((link) => {
      link.addEventListener("click", () => {
        if (!panelByView.has(link.dataset.demoGo)) return;
        if (showcase.classList.contains("is-minimized")) changeWindow("restore", () => {
          showcase.classList.remove("is-minimized");
          setActiveView(link.dataset.demoGo);
        });
        else setActiveView(link.dataset.demoGo);
      });
    });

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

    const changeWindow = (action, change) => {
      const rect = showcase.getBoundingClientRect();
      const before = { width: rect.width, height: rect.height, marginLeft: getComputedStyle(showcase).marginLeft };
      change();
      syncWindowControls();
      showcase.dispatchEvent(new CustomEvent("demowindowchange", { bubbles: true, detail: { action, before } }));
    };

    minimizeButton?.addEventListener("click", () => {
      changeWindow("minimize", () => showcase.classList.add("is-minimized"));
      restoreButton?.focus();
    });

    restoreButton?.addEventListener("click", () => {
      changeWindow("restore", () => showcase.classList.remove("is-minimized"));
      minimizeButton?.focus();
    });

    expandButton?.addEventListener("click", () => {
      changeWindow("expand", () => {
        showcase.classList.remove("is-minimized");
        showcase.classList.toggle("is-expanded");
      });
    });

    resetButton?.addEventListener("click", () => {
      changeWindow("reset", () => {
        showcase.classList.remove("is-minimized", "is-expanded");
        setActiveView("overview");
      });
    });

    syncWindowControls();

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !showcase.classList.contains("is-expanded")) return;
      const focusWasInsideShowcase = showcase.contains(document.activeElement);
      changeWindow("collapse", () => showcase.classList.remove("is-expanded"));
      if (focusWasInsideShowcase) expandButton?.focus();
    });
  }

  const copyButtons = Array.from(document.querySelectorAll("[data-copy-command]"));
  copyButtons.forEach((button) => {
    const originalLabel = button.getAttribute("aria-label");
    let feedbackTimer = 0;
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
      window.clearTimeout(feedbackTimer);
      delete button.dataset.copyState;
      if (originalLabel) button.setAttribute("aria-label", originalLabel);
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

      if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") {
        manualCopy();
        return;
      }

      try {
        await navigator.clipboard.writeText(command);
        window.clearTimeout(feedbackTimer);
        button.dataset.copyState = "copied";
        button.setAttribute("aria-label", "Command copied");
        feedbackTimer = window.setTimeout(() => {
          delete button.dataset.copyState;
          if (originalLabel) button.setAttribute("aria-label", originalLabel);
          else button.removeAttribute("aria-label");
        }, 2400);
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
