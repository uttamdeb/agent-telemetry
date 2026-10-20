(() => {
  "use strict";

  const EVENTS = new Set([
    "at_install_cta_click",
    "at_install_command_copy",
    "at_install_menu_open",
    "at_install_option_click",
  ]);
  const PARAMETERS = [
    "install_option",
    "install_method",
    "placement",
    "destination",
    "release_asset",
    "install_version",
  ];
  const DATA_KEYS = {
    install_option: "analyticsOption",
    install_method: "analyticsMethod",
    placement: "analyticsPlacement",
    destination: "analyticsDestination",
    release_asset: "releaseAsset",
    install_version: "installVersion",
  };

  const safeValue = (value, key) => {
    if (!value) return "";
    const pattern = ["release_asset", "install_version"].includes(key)
      ? /^[a-zA-Z0-9._-]{1,160}$/
      : /^[a-zA-Z0-9_-]{1,64}$/;
    return pattern.test(value) ? value : "";
  };

  const track = (eventName, parameters = {}, callback) => {
    const resume = typeof callback === "function" ? (() => {
      let called = false;
      return () => {
        if (called) return;
        called = true;
        callback();
      };
    })() : null;
    if (!EVENTS.has(eventName) || typeof window.gtag !== "function") {
      resume?.();
      return;
    }

    const payload = {};
    PARAMETERS.forEach((key) => {
      const value = safeValue(parameters[key], key);
      if (value) payload[key] = value;
    });
    if (resume) {
      payload.event_callback = resume;
      payload.event_timeout = 800;
    }
    window.gtag("event", eventName, payload);
  };

  window.AgentTelemetryAnalytics = Object.freeze({ track });

  document.addEventListener("click", (event) => {
    const link = event.target?.closest?.("a[data-analytics-event]");
    if (!link) return;

    const eventName = link.dataset.analyticsEvent;
    if (!EVENTS.has(eventName)) return;
    const parameters = {};
    PARAMETERS.forEach((key) => {
      parameters[key] = link.dataset[DATA_KEYS[key]] || "";
    });

    const url = new URL(link.href, window.location.href);
    const isReleaseDownload = url.hostname === "github.com" && url.pathname.includes("/releases/download/");
    const opensCurrentTab = !link.target || link.target.toLowerCase() === "_self";
    const plainActivation = event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
    if (!isReleaseDownload || !opensCurrentTab || !plainActivation || event.defaultPrevented) {
      track(eventName, parameters);
      return;
    }

    event.preventDefault();
    let continued = false;
    const continueToDownload = () => {
      if (continued) return;
      continued = true;
      window.location.assign(url.href);
    };
    window.setTimeout(continueToDownload, 1000);
    track(eventName, parameters, continueToDownload);
  });
})();
