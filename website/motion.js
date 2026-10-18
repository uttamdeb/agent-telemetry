(() => {
  "use strict";

  const page = document.documentElement;
  const reducedQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  const dashboard = document.querySelector(".dashboard-showcase");
  const chart = dashboard?.querySelector(".demo-chart");
  const tabs = dashboard?.querySelector(".demo-tabs");
  const sidebar = dashboard?.querySelector(".demo-view-nav");
  const menuToggle = document.querySelector("[data-menu-toggle]");
  const revealItems = Array.from(document.querySelectorAll(
    ".dashboard-showcase[data-reveal], .steps[data-reveal], .companion[data-reveal], " +
    ".download-options[data-reveal], .source-download[data-reveal], .team-grid[data-reveal]"
  ));
  const staggerGroups = revealItems.filter((item) => item.matches(".steps, .download-options, .team-grid"));
  const panels = Array.from(dashboard?.querySelectorAll("[data-demo-panel]") || []);
  const detailAnimations = new Map();
  let observer = null;
  let measureFrame = 0;
  let chartFinishTimer = 0;
  let windowAnimation = null;
  let dashboardInView = false;
  let chartDrawn = false;
  let motionEnabled = !reducedQuery.matches;
  let previousView = panels.find((panel) => !panel.hidden)?.dataset.demoPanel;

  function syncMenuState() {
    if (menuToggle) page.classList.toggle("is-menu-closed", menuToggle.getAttribute("aria-expanded") !== "true");
  }

  function syncIndicators() {
    measureFrame = 0;
    if (tabs) {
      const selected = tabs.querySelector('[aria-selected="true"]');
      const visible = Boolean(selected && selected.offsetWidth);
      tabs.classList.toggle("has-active-tab", visible);
      if (visible) {
        tabs.style.setProperty("--active-tab-left", `${selected.offsetLeft}px`);
        tabs.style.setProperty("--active-tab-top", `${selected.offsetTop}px`);
        tabs.style.setProperty("--active-tab-width", `${selected.offsetWidth}px`);
        tabs.style.setProperty("--active-tab-height", `${selected.offsetHeight}px`);
      }
    }
    if (sidebar) {
      const selected = sidebar.querySelector('[aria-pressed="true"]');
      const visible = Boolean(selected && selected.offsetHeight);
      sidebar.classList.toggle("has-active-view", visible);
      if (visible) {
        sidebar.style.setProperty("--active-view-top", `${selected.offsetTop}px`);
        sidebar.style.setProperty("--active-view-height", `${selected.offsetHeight}px`);
      }
    }
  }

  function scheduleMeasure() {
    if (!measureFrame) measureFrame = window.requestAnimationFrame(syncIndicators);
  }

  function reveal(item) {
    const entering = item.classList.contains("motion-reveal") && !item.classList.contains("is-visible");
    item.classList.add("is-visible");
    if (entering && motionEnabled && staggerGroups.includes(item)) item.classList.add("motion-staggered");
  }

  function drawChart() {
    const overview = panels.find((panel) => panel.dataset.demoPanel === "overview");
    if (!chart || chartDrawn || !dashboardInView || overview?.hidden || !motionEnabled) return;
    chartDrawn = true;
    chart.classList.add("motion-chart-run");
    // Clearing the finite trace also settles a chart hidden by a rapid view change.
    chartFinishTimer = window.setTimeout(() => chart.classList.remove("motion-chart-run"), 900);
  }

  function observeScenes() {
    observer?.disconnect();
    if (!("IntersectionObserver" in window)) {
      revealItems.forEach(reveal);
      chartDrawn = true;
      return;
    }
    observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.target === dashboard) {
          dashboardInView = entry.isIntersecting;
          if (entry.isIntersecting) {
            reveal(dashboard);
            drawChart();
          }
          if (chartDrawn) observer.unobserve(dashboard);
        } else if (entry.isIntersecting) {
          reveal(entry.target);
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: .04, rootMargin: "0px 0px 64px 0px" });
    revealItems.forEach((item) => {
      if (item === dashboard || !item.classList.contains("is-visible")) observer.observe(item);
    });
  }

  function finishDetails(details, state) {
    if (detailAnimations.get(details) !== state) return;
    details.open = state.open;
    details.style.removeProperty("overflow");
    detailAnimations.delete(details);
  }

  function animateDetails(event) {
    const summary = event.target.closest?.("summary");
    const details = summary?.parentElement;
    if (!details?.matches(".faq-list > details, .command-alternative") ||
        !motionEnabled || typeof details.animate !== "function") return;
    event.preventDefault();
    const previous = detailAnimations.get(details);
    const open = previous ? !previous.open : !details.open;
    const startHeight = details.getBoundingClientRect().height;
    previous?.animation.cancel();
    // Native details stays open during collapse; rapid clicks reverse the current height.
    details.open = true;
    const style = getComputedStyle(details);
    const border = (parseFloat(style.borderTopWidth) || 0) + (parseFloat(style.borderBottomWidth) || 0);
    const endHeight = open ? details.getBoundingClientRect().height : summary.getBoundingClientRect().height + border;
    details.style.overflow = "hidden";
    const animation = details.animate([{ height: `${startHeight}px` }, { height: `${endHeight}px` }], {
      duration: 280, easing: "cubic-bezier(.22,1,.36,1)"
    });
    const state = { animation, open };
    detailAnimations.set(details, state);
    animation.onfinish = () => finishDetails(details, state);
  }

  function onDemoChange(event) {
    if (event.target !== dashboard) return;
    syncIndicators();
    const view = event.detail?.view;
    const panel = panels.find((item) => item.dataset.demoPanel === view);
    const previousIndex = panels.findIndex((item) => item.dataset.demoPanel === previousView);
    const nextIndex = panels.indexOf(panel);
    const changed = view !== previousView;
    previousView = view;
    if (!motionEnabled || !panel || panel.hidden || !changed) return;
    panels.forEach((item) => item.classList.remove("motion-arriving"));
    panel.style.setProperty("--panel-direction", nextIndex < previousIndex ? "-1" : "1");
    // The selected panel is already available; motion never gates its controls or content.
    panel.classList.add("motion-arriving");
    if (view === "overview") drawChart();
  }

  function onWindowChange(event) {
    if (event.target !== dashboard) return;
    scheduleMeasure();
    if (!motionEnabled || !event.detail?.before || typeof dashboard.animate !== "function") return;
    const before = event.detail.before;
    windowAnimation?.cancel();
    const after = dashboard.getBoundingClientRect();
    const marginLeft = getComputedStyle(dashboard).marginLeft;
    if (Math.abs(before.width - after.width) < 1 && Math.abs(before.height - after.height) < 1) return;
    windowAnimation = dashboard.animate([
      { width: `${before.width}px`, height: `${before.height}px`, marginLeft: before.marginLeft },
      { width: `${after.width}px`, height: `${after.height}px`, marginLeft }
    ], { duration: 280, easing: "cubic-bezier(.22,1,.36,1)" });
    const current = windowAnimation;
    current.onfinish = () => {
      if (windowAnimation === current) windowAnimation = null;
      scheduleMeasure();
    };
  }

  function handleReducedMotion() {
    motionEnabled = !reducedQuery.matches;
    page.classList.toggle("motion-reduced", !motionEnabled);
    if (motionEnabled) {
      observeScenes();
      scheduleMeasure();
      return;
    }
    page.classList.add("motion-intro-complete");
    observer?.disconnect();
    revealItems.forEach(reveal);
    detailAnimations.forEach((state, details) => {
      state.animation.cancel();
      finishDetails(details, state);
    });
    windowAnimation?.cancel();
    windowAnimation = null;
    panels.forEach((panel) => panel.classList.remove("motion-arriving"));
    window.clearTimeout(chartFinishTimer);
    chart?.classList.remove("motion-chart-run");
    chartDrawn = true;
    syncIndicators();
  }

  staggerGroups.forEach((group) => {
    Array.from(group.children).forEach((item, index) => item.style.setProperty("--motion-index", String(Math.min(index, 3))));
  });
  revealItems.forEach((item) => {
    if (motionEnabled && item.getBoundingClientRect().top > window.innerHeight + 12) item.classList.add("motion-reveal");
    else item.classList.add("is-visible");
  });
  if (dashboard && motionEnabled && dashboard.getBoundingClientRect().top < window.innerHeight) {
    dashboard.classList.add("motion-first-view");
  }
  chart?.querySelectorAll(".chart-line").forEach((path) => {
    try {
      path.style.setProperty("--motion-chart-length", String(path.getTotalLength()));
    } catch (error) {
      console.warn("Could not measure the sample chart for animation.", error);
    }
  });

  syncMenuState();
  if (menuToggle && "MutationObserver" in window) {
    new MutationObserver(syncMenuState).observe(menuToggle, { attributes: true, attributeFilter: ["aria-expanded"] });
  }
  document.addEventListener("click", animateDetails);
  document.addEventListener("demochange", onDemoChange);
  document.addEventListener("demowindowchange", onWindowChange);
  dashboard?.addEventListener("animationend", (event) => {
    if (event.target.matches?.(".demo-panel")) event.target.classList.remove("motion-arriving");
  });
  window.addEventListener("resize", () => {
    // A resize invalidates the pixel dimensions captured for a window transition.
    // Release them immediately so a desktop animation cannot overflow a phone layout.
    windowAnimation?.cancel();
    windowAnimation = null;
    scheduleMeasure();
  }, { passive: true });
  if ("ResizeObserver" in window) {
    const resizeObserver = new ResizeObserver(scheduleMeasure);
    [dashboard, tabs, sidebar].filter(Boolean).forEach((item) => resizeObserver.observe(item));
  }
  const syncVisibility = () => page.classList.toggle("motion-suspended", document.hidden);
  document.addEventListener("visibilitychange", syncVisibility);
  syncVisibility();
  page.classList.add("motion-ready");
  if (motionEnabled) observeScenes();
  else handleReducedMotion();
  syncIndicators();
  window.setTimeout(() => page.classList.add("motion-intro-complete"), 900);
  if (reducedQuery.addEventListener) reducedQuery.addEventListener("change", handleReducedMotion);
  else reducedQuery.addListener(handleReducedMotion);
})();
