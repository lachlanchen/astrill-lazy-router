const state = {
  catalog: [],
  stable: null,
  release: null,
  selected: new Set(),
  routes: new Map(),
  query: "",
  country: "",
  category: "",
  routeFilter: "",
};

const elements = {
  body: document.querySelector("#catalog-body"),
  template: document.querySelector("#service-row"),
  search: document.querySelector("#search"),
  country: document.querySelector("#country-filter"),
  category: document.querySelector("#category-filter"),
  selectVisible: document.querySelector("#select-visible"),
  selectionCount: document.querySelector("#selection-count"),
  visibleCount: document.querySelector("#visible-count"),
  policyHash: document.querySelector("#policy-hash"),
  releaseDot: document.querySelector("#release-dot"),
  releaseLabel: document.querySelector("#release-label"),
  releaseSummary: document.querySelector("#release-summary"),
  installCommand: document.querySelector("#install-command"),
};

function searchText(service) {
  return [
    service.name,
    service.company,
    service.provider_country,
    service.category,
    service.profile_type,
    ...service.aliases,
    ...service.domains,
  ].join(" ").toLocaleLowerCase();
}

function visibleServices() {
  return state.catalog.filter((service) => {
    const route = state.routes.get(service.id)?.route || service.default_route;
    return (!state.query || service.search_text.includes(state.query))
      && (!state.country || service.provider_country === state.country)
      && (!state.category || service.category === state.category)
      && (!state.routeFilter || route === state.routeFilter);
  });
}

function routeLabel(route) {
  return route === "direct" ? "Direct" : "Astrill";
}

function regionLabel(region) {
  if (region === "direct") return "Direct WAN";
  if (region === "active-astrill") return "Active endpoint";
  return region.split("-").map((part) => part[0].toUpperCase() + part.slice(1)).join(" ");
}

function render() {
  const visible = visibleServices();
  elements.body.replaceChildren();
  const fragment = document.createDocumentFragment();
  for (const service of visible) {
    const row = elements.template.content.firstElementChild.cloneNode(true);
    const route = state.routes.get(service.id) || {
      route: service.default_route,
      region: service.preferred_region,
    };
    row.dataset.serviceId = service.id;
    row.classList.toggle("selected", state.selected.has(service.id));
    const checkbox = row.querySelector(".row-select");
    checkbox.checked = state.selected.has(service.id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selected.add(service.id);
      else state.selected.delete(service.id);
      render();
    });
    row.querySelector(".service-name").textContent = service.name;
    row.querySelector(".service-type").textContent = service.profile_type;
    row.querySelector(".company").textContent = service.company;
    row.querySelector(".country").textContent = service.provider_country;
    row.querySelector(".category").textContent = service.category;
    const choice = row.querySelector(".route-choice");
    choice.value = route.route;
    choice.classList.add(route.route);
    choice.setAttribute("aria-label", `${service.name} route`);
    choice.addEventListener("change", () => {
      const nextRoute = choice.value;
      state.routes.set(service.id, {
        route: nextRoute,
        region: nextRoute === "direct"
          ? "direct"
          : service.preferred_region === "direct"
            ? "active-astrill"
            : service.preferred_region,
      });
      state.selected.add(service.id);
      render();
    });
    row.querySelector(".region").textContent = regionLabel(route.region);
    fragment.append(row);
  }
  elements.body.append(fragment);
  elements.visibleCount.textContent = `${visible.length} of ${state.catalog.length} services`;
  elements.selectionCount.textContent = `${state.selected.size} selected`;
  const visibleSelected = visible.filter((item) => state.selected.has(item.id)).length;
  elements.selectVisible.checked = visible.length > 0 && visibleSelected === visible.length;
  elements.selectVisible.indeterminate = visibleSelected > 0 && visibleSelected < visible.length;
}

function loadStable() {
  state.selected.clear();
  state.routes.clear();
  for (const rule of state.stable.rules) {
    state.selected.add(rule.service_id);
    state.routes.set(rule.service_id, {
      route: rule.route,
      region: rule.region,
      originId: rule.origin_id,
      priority: rule.priority,
    });
  }
  render();
}

function setSelectedRoute(route) {
  for (const id of state.selected) {
    const service = state.catalog.find((item) => item.id === id);
    if (!service) continue;
    state.routes.set(id, {
      route,
      region: route === "direct"
        ? "direct"
        : service.preferred_region === "direct"
          ? "active-astrill"
          : service.preferred_region,
    });
  }
  render();
}

function customBundle() {
  const entries = state.catalog
    .filter((service) => state.selected.has(service.id))
    .map((service, index) => {
      const choice = state.routes.get(service.id) || {
        route: service.default_route,
        region: service.preferred_region,
      };
      const stable = state.stable.rules.find((rule) => rule.service_id === service.id);
      return {
        enabled: true,
        origin_id: stable?.origin_id || service.id,
        priority: stable?.priority ?? Math.min(9999, 100 + index * 30),
        region: choice.region,
        route: choice.route,
        service_id: service.id,
      };
    });
  return {
    bundle_id: "daily-custom",
    catalog: "core-catalog",
    description: "Custom catalog-only policy exported from the Astrill Lazy policy workspace.",
    rules: entries,
    schema_version: 1,
    version: `custom-${new Date().toISOString().slice(0, 10).replaceAll("-", "")}`,
  };
}

function downloadPolicy() {
  const bundle = customBundle();
  if (!bundle.rules.length) {
    elements.selectionCount.textContent = "Select at least one service";
    return;
  }
  const blob = new Blob([`${JSON.stringify(bundle, null, 2)}\n`], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "astrill-lazy-custom-policy.json";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function populateFilter(select, values) {
  for (const value of [...new Set(values)].sort((a, b) => a.localeCompare(b))) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.append(option);
  }
}

function wireEvents() {
  elements.search.addEventListener("input", () => {
    state.query = elements.search.value.trim().toLocaleLowerCase();
    render();
  });
  elements.country.addEventListener("change", () => {
    state.country = elements.country.value;
    render();
  });
  elements.category.addEventListener("change", () => {
    state.category = elements.category.value;
    render();
  });
  document.querySelectorAll("[data-route-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-route-filter]").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      state.routeFilter = button.dataset.routeFilter;
      render();
    });
  });
  elements.selectVisible.addEventListener("change", () => {
    for (const service of visibleServices()) {
      if (elements.selectVisible.checked) state.selected.add(service.id);
      else state.selected.delete(service.id);
    }
    render();
  });
  document.querySelector("#set-direct").addEventListener("click", () => setSelectedRoute("direct"));
  document.querySelector("#set-vpn").addEventListener("click", () => setSelectedRoute("vpn"));
  document.querySelector("#reset-stable").addEventListener("click", loadStable);
  document.querySelector("#download-policy").addEventListener("click", downloadPolicy);
  document.querySelector("#copy-command").addEventListener("click", async () => {
    await navigator.clipboard.writeText(elements.installCommand.textContent);
    document.querySelector("#copy-command span").textContent = "Copied";
    window.setTimeout(() => {
      document.querySelector("#copy-command span").textContent = "Copy";
    }, 1400);
  });
}

async function start() {
  try {
    const [catalogResponse, releaseResponse] = await Promise.all([
      fetch("data/catalog.json", { cache: "no-store" }),
      fetch("data/release.json", { cache: "no-store" }),
    ]);
    if (!catalogResponse.ok || !releaseResponse.ok) throw new Error("Release metadata unavailable");
    state.catalog = (await catalogResponse.json()).services;
    state.release = await releaseResponse.json();
    const policyResponse = await fetch(state.release.policy_url, { cache: "no-store" });
    if (!policyResponse.ok) throw new Error("Stable policy unavailable");
    state.stable = await policyResponse.json();
    state.catalog = state.catalog.map((service) => ({ ...service, search_text: searchText(service) }));
    populateFilter(elements.country, state.catalog.map((item) => item.provider_country));
    populateFilter(elements.category, state.catalog.map((item) => item.category));
    elements.releaseDot.classList.add("ready");
    elements.releaseLabel.textContent = `${state.stable.bundle_id} ${state.stable.version} - verified`;
    elements.releaseSummary.textContent =
      `${state.stable.rules.length} selected profiles from ${state.catalog.length} available services.`;
    elements.policyHash.textContent = `SHA-256 ${state.release.policy_sha256}`;
    elements.installCommand.textContent =
      `astrill-lazy policy-bundle apply ${state.release.policy_absolute_url} --sha256 ${state.release.policy_sha256}`;
    wireEvents();
    loadStable();
  } catch (error) {
    elements.releaseDot.classList.add("error");
    elements.releaseLabel.textContent = "Release unavailable";
    elements.releaseSummary.textContent = error.message;
    elements.installCommand.textContent = "Could not load verified release metadata.";
  }
}

start();
