(function () {
  "use strict";

  const queryTab = new URLSearchParams(window.location.search).get("tab");
  const hashTab = window.location.hash
    .replace(/^#\/?/, "")
    .split(/[/?#]/, 1)[0];
  const tab = queryTab || hashTab;
  const allowedTabs = new Set(["documents", "knowledge-graph", "retrieval", "api"]);
  if (!allowedTabs.has(tab)) {
    return;
  }

  const storageKey = "settings-storage";
  try {
    const stored = JSON.parse(window.localStorage.getItem(storageKey) || "null") || {};
    const state = stored.state && typeof stored.state === "object" ? stored.state : {};
    stored.state = { ...state, currentTab: tab };
    stored.version = Number.isInteger(stored.version) ? stored.version : 19;
    window.localStorage.setItem(storageKey, JSON.stringify(stored));
  } catch (error) {
    console.warn("Unable to select the requested LightRAG tab", error);
  }
})();
