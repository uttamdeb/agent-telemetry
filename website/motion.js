(() => {
  "use strict";

  const page = document.documentElement;
  const reducedQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  const revealItems = Array.from(document.querySelectorAll("[data-reveal]"));
  const staggerGroups = Array.from(document.querySelectorAll(".steps, .team-grid, .tool-list, .download-options"));
  const sceneElements = Array.from(document.querySelectorAll(".hero-art, .privacy-art, .companion-visual, .download-orbit"));
  const counters = Array.from(document.querySelectorAll("[data-count]"));
  const counterAnimations = new Map();
  const detailAnimations = new Map();
  const dashboard = document.querySelector(".dashboard-showcase");
  const chart = dashboard && dashboard.querySelector(".demo-chart");
  const tabs = dashboard && dashboard.querySelector(".demo-tabs");
  const menuToggle = document.querySelector("[data-menu-toggle]");
  const siteNav = document.getElementById("site-nav");
  const scenes = sceneElements.map((element) => ({ element, top: 0, height: 0, visible: false }));
  let intersectionObserver = null;
  let scrollFrame = 0;
  let resizeFrame = 0;
  let maxScroll = 0;
  let motionEnabled = false;
  let listenersAttached = false;
  let chartLengths = [];
  let menuObserver = null;
  let pendingPanelFrames = new WeakMap();

  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

  function syncMenuState() {
    if (!menuToggle || !siteNav) return;
    page.classList.toggle("is-menu-closed", menuToggle.getAttribute("aria-expanded") !== "true");
  }

  function measurePage() {
    const scrollY = window.scrollY || window.pageYOffset || 0;
    maxScroll = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
    scenes.forEach((scene) => {
      const rect = scene.element.getBoundingClientRect();
      scene.top = rect.top + scrollY;
      scene.height = rect.height;
    });
    syncTabIndicator();
    updateMotion();
  }

  function updateMotion() {
    scrollFrame = 0;
    if (!motionEnabled || reducedQuery.matches) return;

    const scrollY = window.scrollY || window.pageYOffset || 0;
    page.style.setProperty("--page-progress", String(clamp(scrollY / maxScroll, 0, 1)));

    const viewportCenter = scrollY + window.innerHeight * 0.5;
    scenes.forEach((scene) => {
      if (!scene.visible) return;
      const sceneCenter = scene.top + scene.height * 0.5;
      const distance = clamp((sceneCenter - viewportCenter) / Math.max(1, window.innerHeight), -0.6, 0.6);
      scene.element.style.setProperty("--motion-scene-y", `${Math.round(distance * -42)}px`);
    });
  }

  function scheduleScroll() {
    if (!scrollFrame) scrollFrame = window.requestAnimationFrame(updateMotion);
  }

  function scheduleMeasure() {
    if (resizeFrame) return;
    resizeFrame = window.requestAnimationFrame(() => {
      resizeFrame = 0;
      measurePage();
    });
  }

  function reveal(element, observer) {
    element.classList.add("is-visible");
    if (observer) observer.unobserve(element);
  }

  function markStaggered(group, observer) {
    group.classList.add("motion-staggered");
    if (observer) observer.unobserve(group);
  }

  function staggerItems(group) {
    const selector = group.matches(".steps") ? ":scope > .step"
      : group.matches(".download-options") ? ":scope > .download-platform" : ":scope > li";
    return group.querySelectorAll(selector);
  }

  function syncTabIndicator() {
    if (!tabs) return;
    const selected = tabs.querySelector('[aria-selected="true"]');
    tabs.classList.toggle("has-active-tab", Boolean(selected));
    if (!selected) return;
    tabs.style.setProperty("--active-tab-left", `${selected.offsetLeft}px`);
    tabs.style.setProperty("--active-tab-width", `${selected.offsetWidth}px`);
  }

  function animateCount(element) {
    if (reducedQuery.matches || element.dataset.counted) return;
    const node = Array.from(element.childNodes).find((child) => child.nodeType === Node.TEXT_NODE && child.textContent.trim());
    const value = Number(element.dataset.count);
    if (!node || !Number.isFinite(value)) return;
    element.dataset.counted = "true";
    const state = { node, original: node.textContent, frame: 0, start: null };
    counterAnimations.set(element, state);
    const decimals = Number(element.dataset.decimals) || 0;
    const prefix = element.dataset.prefix || "";
    const tick = (time) => {
      if (state.start === null) state.start = time;
      const progress = clamp((time - state.start) / 1100, 0, 1);
      node.textContent = prefix + (value * (1 - Math.pow(1 - progress, 3))).toFixed(decimals);
      if (progress < 1 && !reducedQuery.matches) state.frame = requestAnimationFrame(tick);
      else {
        node.textContent = state.original;
        counterAnimations.delete(element);
      }
    };
    state.frame = requestAnimationFrame(tick);
  }

  function finishDetails(details, state) {
    if (detailAnimations.get(details) !== state) return;
    details.open = state.open;
    details.style.removeProperty("overflow");
    detailAnimations.delete(details);
    scheduleMeasure();
  }

  function animateDetails(event) {
    const summary = event.target.closest && event.target.closest("summary");
    const details = summary && summary.parentElement;
    if (!details || !details.matches(".faq-list > details, .command-alternative") ||
        reducedQuery.matches || typeof details.animate !== "function") return;
    event.preventDefault();
    const previous = detailAnimations.get(details);
    const open = previous ? !previous.open : !details.open;
    const startHeight = details.getBoundingClientRect().height;
    if (previous) previous.animation.cancel();
    // Keep native details semantics and content available during collapse, then
    // commit its closed state only after the height transition completes.
    details.open = true;
    const border = parseFloat(getComputedStyle(details).borderTopWidth) + parseFloat(getComputedStyle(details).borderBottomWidth);
    const endHeight = open ? details.getBoundingClientRect().height : summary.getBoundingClientRect().height + border;
    details.style.overflow = "hidden";
    const animation = details.animate([{ height: `${startHeight}px` }, { height: `${endHeight}px` }], {
      duration: 380, easing: "cubic-bezier(.22,1,.36,1)"
    });
    const state = { animation, open };
    detailAnimations.set(details, state);
    animation.onfinish = () => finishDetails(details, state);
    scheduleMeasure();
  }

  function drawChart() {
    if (!chart || reducedQuery.matches || !dashboard || !dashboard.classList.contains("motion-in-view")) return;
    chart.classList.remove("motion-chart-run");
    chartLengths.forEach(({ path, length }) => {
      path.style.strokeDasharray = `${length}`;
      path.style.strokeDashoffset = `${length}`;
    });

    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (!chart.isConnected || reducedQuery.matches) return;
      chart.classList.add("motion-chart-run");
    }));
  }

  function finishChart(event) {
    if (!event.target.matches || !event.target.matches(".chart-line")) return;
    const path = event.target;
    path.style.strokeDashoffset = "0";
    if (chartLengths.length && chartLengths.every(({ path: line }) => line.style.strokeDashoffset === "0")) {
      chart.classList.remove("motion-chart-run");
    }
  }

  function measureChart() {
    if (!chart) return;
    chartLengths = Array.from(chart.querySelectorAll(".chart-line")).map((path) => {
      let length = 1200;
      try {
        length = path.getTotalLength();
      } catch (error) {
        console.warn("Could not measure the sample chart path for animation.", error);
      }
      path.style.setProperty("--motion-chart-length", `${length}`);
      return { path, length };
    });
  }

  function enterPanel(view) {
    if (!dashboard || !view || reducedQuery.matches) return;
    const panel = Array.from(dashboard.querySelectorAll("[data-demo-panel]")).find((candidate) =>
      candidate.dataset.demoPanel === String(view) || candidate.id === String(view)
    );
    if (!panel || panel.hidden) return;

    panel.querySelectorAll(".demo-metric, .session-row, .device-card, .model-view-row, .tool-view-card, .project-view-card, .optimize-view-row").forEach((row, index) => {
      row.style.setProperty("--row-index", String(Math.min(index, 7)));
    });

    const existing = pendingPanelFrames.get(panel);
    if (existing) {
      cancelAnimationFrame(existing.first);
      cancelAnimationFrame(existing.second);
    }
    panel.classList.remove("motion-arriving");
    const frames = { first: 0, second: 0 };
    pendingPanelFrames.set(panel, frames);
    frames.first = requestAnimationFrame(() => {
      frames.second = requestAnimationFrame(() => {
        if (!panel.isConnected || panel.hidden || reducedQuery.matches) return;
        panel.classList.add("motion-arriving");
      });
    });
  }

  function onDemoChange(event) {
    const showcase = event.target && event.target.closest && event.target.closest(".dashboard-showcase");
    if (showcase && dashboard && showcase !== dashboard) return;
    if (!showcase && event.target !== document) return;
    const view = event.detail && event.detail.view;
    syncTabIndicator();
    if (view === "overview") drawChart();
    enterPanel(view);
    scheduleMeasure();
  }

  function onLayoutClick(event) {
    const control = event.target.closest && event.target.closest("[data-demo-window], [data-demo-tab], [data-copy-command]");
    if (control) scheduleMeasure();
  }

  function onDetailsToggle(event) {
    if (event.target.matches && event.target.matches("details")) scheduleMeasure();
  }

  function onDashboardTransitionEnd(event) {
    if (event.target === dashboard) scheduleMeasure();
  }

  function onLayoutKeyDown(event) {
    if (event.key === "Escape") scheduleMeasure();
  }

  function onClick(event) {
    if (reducedQuery.matches) return;
    const action = event.target.closest && event.target.closest(
      "button:not(:disabled), .button, .download-link, .install-option, .nav-cta, .text-link, .site-nav a, .hero-foot a, [role='button']"
    );
    if (!action || action.getAttribute("aria-disabled") === "true") return;

    const bounds = action.getBoundingClientRect();
    const x = Number.isFinite(event.clientX) && event.clientX ? event.clientX : bounds.left + bounds.width / 2;
    const y = Number.isFinite(event.clientY) && event.clientY ? event.clientY : bounds.top + bounds.height / 2;
    const diameter = Math.ceil(Math.hypot(bounds.width, bounds.height) * 1.5);
    const ripple = document.createElement("span");
    ripple.className = "motion-ripple";
    ripple.setAttribute("aria-hidden", "true");
    ripple.style.left = `${x - bounds.left}px`;
    ripple.style.top = `${y - bounds.top}px`;
    ripple.style.width = `${diameter}px`;
    ripple.style.height = `${diameter}px`;
    action.appendChild(ripple);
    ripple.addEventListener("animationend", () => ripple.remove(), { once: true });
    window.setTimeout(() => ripple.remove(), 650);
    if (action.closest(".dashboard-showcase") && action.matches("[data-demo-window]")) scheduleMeasure();
  }

  function handleReducedMotion() {
    const reduced = reducedQuery.matches;
    page.classList.toggle("motion-reduced", reduced);
    if (reduced) {
      motionEnabled = false;
      setMotionListeners(false);
      if (intersectionObserver) intersectionObserver.disconnect();
      intersectionObserver = null;
      revealItems.forEach((item) => reveal(item));
      staggerGroups.forEach((group) => markStaggered(group));
      scenes.forEach((scene) => {
        scene.visible = false;
        scene.element.style.removeProperty("--motion-scene-y");
      });
      counterAnimations.forEach((state) => {
        cancelAnimationFrame(state.frame);
        state.node.textContent = state.original;
      });
      counterAnimations.clear();
      detailAnimations.forEach((state, details) => {
        state.animation.cancel();
        finishDetails(details, state);
      });
      document.querySelectorAll(".motion-ripple").forEach((ripple) => ripple.remove());
      if (chart) {
        chart.classList.remove("motion-chart-run");
        chartLengths.forEach(({ path }) => path.style.strokeDashoffset = "0");
      }
      return;
    }

    page.classList.add("motion-ready");
    motionEnabled = true;
    setMotionListeners(true);
    observeMotionTargets();
    scheduleMeasure();
  }

  function setMotionListeners(attach) {
    if (attach === listenersAttached) return;
    listenersAttached = attach;
    const method = attach ? "addEventListener" : "removeEventListener";
    window[method]("scroll", scheduleScroll, { passive: true });
    window[method]("resize", scheduleMeasure, { passive: true });
    document[method]("click", onClick, { passive: true });
    document[method]("click", onLayoutClick, { passive: true });
    document[method]("click", animateDetails);
    document[method]("demochange", onDemoChange);
    document[method]("toggle", onDetailsToggle, true);
    document[method]("keydown", onLayoutKeyDown, true);
    if (dashboard) dashboard[method]("transitionend", onDashboardTransitionEnd);
    if (chart) chart[method]("animationend", finishChart);
  }

  function observeMotionTargets() {
    if (!("IntersectionObserver" in window)) {
      revealItems.forEach((item) => reveal(item));
      staggerGroups.forEach((group) => markStaggered(group));
      scenes.forEach((scene) => {
        scene.visible = true;
        scene.element.classList.add("motion-in-view");
      });
      if (dashboard) dashboard.classList.add("motion-in-view");
      chartLengths.forEach(({ path }) => path.style.strokeDashoffset = "0");
      return;
    }

    if (intersectionObserver) intersectionObserver.disconnect();
    intersectionObserver = new IntersectionObserver((entries, observer) => {
      entries.forEach((entry) => {
        const target = entry.target;
        const isDashboard = target === dashboard;
        const isStaggerGroup = staggerGroups.includes(target);
        const scene = scenes.find((item) => item.element === target);

        if (target.matches("[data-count]") && entry.isIntersecting) {
          animateCount(target);
          observer.unobserve(target);
          return;
        }

        if (target.matches("[data-reveal]") && entry.isIntersecting) {
          target.classList.add("is-visible");
          if (!isDashboard && !isStaggerGroup) observer.unobserve(target);
        }

        if (scene) {
          scene.visible = entry.isIntersecting;
          target.classList.toggle("motion-in-view", scene.visible);
          if (scene.visible) {
            scheduleScroll();
          } else {
            target.style.removeProperty("--motion-scene-y");
          }
          return;
        }

        if (isDashboard) {
          dashboard.classList.toggle("motion-in-view", entry.isIntersecting);
          if (entry.isIntersecting) {
            const activePanel = dashboard.querySelector('[data-demo-panel]:not([hidden])');
            if (activePanel && activePanel.dataset.demoPanel === "overview") drawChart();
          }
          return;
        }

        if (isStaggerGroup && entry.isIntersecting) markStaggered(target, observer);
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -5% 0px" });

    revealItems.forEach((item) => {
      if (!item.classList.contains("is-visible")) intersectionObserver.observe(item);
    });
    staggerGroups.forEach((group) => {
      Array.from(staggerItems(group)).forEach((item, index) => item.style.setProperty("--motion-index", String(index)));
      if (!group.classList.contains("motion-staggered")) intersectionObserver.observe(group);
    });
    scenes.forEach(({ element, visible }) => {
      if (visible) element.classList.add("motion-in-view");
      intersectionObserver.observe(element);
    });
    counters.filter((element) => !element.dataset.counted).forEach((element) => intersectionObserver.observe(element));
    if (dashboard) intersectionObserver.observe(dashboard);
  }

  function init() {
    const syncVisibility = () => page.classList.toggle("motion-suspended", document.hidden);
    document.addEventListener("visibilitychange", syncVisibility);
    syncVisibility();
    syncMenuState();
    if (menuToggle && siteNav && "MutationObserver" in window) {
      menuObserver = new MutationObserver(syncMenuState);
      menuObserver.observe(menuToggle, { attributes: true, attributeFilter: ["aria-expanded"] });
    }

    staggerGroups.forEach((group) => {
      Array.from(staggerItems(group)).forEach((item, index) => item.style.setProperty("--motion-index", String(index)));
    });

    measureChart();
    if (!reducedQuery.matches) {
      page.classList.add("motion-ready");
      motionEnabled = true;
      setMotionListeners(true);
      observeMotionTargets();
      measurePage();
    } else {
      motionEnabled = false;
      setMotionListeners(false);
      page.classList.add("motion-reduced");
      revealItems.forEach((item) => reveal(item));
      staggerGroups.forEach((group) => markStaggered(group));
      if (chart) chartLengths.forEach(({ path }) => path.style.strokeDashoffset = "0");
    }

    if (reducedQuery.addEventListener) reducedQuery.addEventListener("change", handleReducedMotion);
    else reducedQuery.addListener(handleReducedMotion);

    window.addEventListener("load", () => {
      if (!reducedQuery.matches) scheduleMeasure();
    }, { once: true });
  }

  init();
})();
