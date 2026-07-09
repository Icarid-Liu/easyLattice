const form = document.querySelector("#parameter-form");
const statusPill = document.querySelector("#status-pill");
const title = document.querySelector("#summary-title");
const subtitle = document.querySelector("#summary-subtitle");
const resultGrid = document.querySelector("#result-grid");
const details = document.querySelector("#details");
const warnings = document.querySelector("#warnings");
const alternatives = document.querySelector("#alternatives");
const jsonPanel = document.querySelector("#json-panel");
const jsonOutput = document.querySelector("#json-output");
const copyJson = document.querySelector("#copy-json");
const nttScale = document.querySelector("#ntt-scale");
const nttScaleLabel = document.querySelector("#ntt-scale-label");
const ringFamily = document.querySelector("#ring-family");
const distributionSelect = document.querySelector("#distribution");
const useLLM = document.querySelector("#use-llm");
const profilePanel = document.querySelector("#profile-panel");

let lastResult = null;
const DEFAULT_DISTRIBUTION_OPTIONS = [
  ["auto", "Auto"],
  ["centered_binomial", "Centered Binomial"],
  ["sparse_ternary", "Sparse Ternary"],
];
const HARD_PROBLEM_VARIANT_LABELS = {
  matrix: "matrix",
  ring: "ring",
  lwe: "LWE",
  rlwe: "RLWE",
  lwr: "LWR",
  rlwr: "RLWR",
  mlwe: "MLWE",
  mlwr: "MLWR",
  sis: "SIS",
  msis: "MSIS",
};

loadPublicConfig();
syncDistributionOptions();
updateNttScaleLabel();

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  await requestRecommendation();
});

copyJson.addEventListener("click", async () => {
  if (!lastResult) return;
  await navigator.clipboard.writeText(JSON.stringify(lastResult.recommendation, null, 2));
  copyJson.textContent = "Copied";
  setTimeout(() => {
    copyJson.textContent = "Copy JSON";
  }, 1200);
});

nttScale.addEventListener("input", updateNttScaleLabel);
ringFamily.addEventListener("change", updateNttScaleLabel);
document.querySelectorAll('input[name="hardProblem"]').forEach((input) => {
  input.addEventListener("change", syncDistributionOptions);
});

async function requestRecommendation() {
  setStatus("loading", "Running");
  title.textContent = "Searching parameters";
  subtitle.textContent = "Generating NTT-friendly moduli and screening security estimates.";

  const data = new FormData(form);
  const hardProblem = selectedHardProblem(data);
  const useEstimator = data.get("useEstimator") === "on";
  if (useEstimator) {
    subtitle.textContent = "Running lattice-estimator validation. This can take several minutes.";
  }
  const payload = {
    problem: hardProblem.category === "ntru" ? "ntru" : "rlwe",
    hardProblemCategory: hardProblem.category,
    hardProblemVariant: hardProblem.variant,
    ringFamily: data.get("ringFamily"),
    targetSecurity: Number(data.get("targetSecurity")),
    securityModel: data.get("securityModel"),
    redCostModel: data.get("redCostModel"),
    nttScalePower: Number(data.get("nttScalePower")),
    minQBits: Number(data.get("minQBits")),
    maxQBits: Number(data.get("maxQBits")),
    distribution: data.get("distribution"),
    useEstimator,
    intent: String(data.get("intent") || ""),
    useLLM: data.get("useLLM") === "on",
  };

  try {
    const response = await fetch("/api/agent/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "request failed");
    }
    lastResult = result;
    renderResult(result);
    setStatus("done", "Ready");
  } catch (error) {
    setStatus("error", "Error");
    title.textContent = "Request failed";
    subtitle.textContent = error.message;
  }
}

function renderResult(result) {
  const candidate = result.recommendation;
  const target = result.request.target_security;
  const source = candidate.security.source === "sage-lattice-estimator" ? "Sage estimator" : "fast screen";

  title.textContent = `Recommended lattice instance (${candidate.ring.n}, ${candidate.modulus.q})`;
  subtitle.textContent = `${source} · ${result.search.generated_candidates} candidates · ${result.search.elapsed_ms} ms`;

  show(resultGrid, details, warnings, alternatives, jsonPanel);
  setText("#classic-bits", `${candidate.security.classical_bits} bits`);
  setText("#quantum-bits", `${candidate.security.quantum_bits} bits`);
  setText("#ring-n", String(candidate.ring.n));
  setText("#ring-poly", candidate.ring.polynomial);
  setText("#modulus-q", String(candidate.modulus.q));
  setText("#modulus-bits", `${candidate.modulus.bits} bits`);
  setText("#selected-bits", `${candidate.selection.selected_security_bits} bits`);
  setText("#security-margin", `margin +${candidate.selection.margin_bits} bits`);
  setMeter("#classic-meter", candidate.security.classical_bits, target);
  setMeter("#quantum-meter", candidate.security.quantum_bits, target);
  renderParameterProfile(candidate);

  const instanceRows = [
    ["Hard problem", formatHardProblem(result.request)],
    ["Family", candidate.ring.family],
    ["Ring", candidate.ring.quotient],
    ["Cyclotomic", `Φ_${candidate.ring.cyclotomic_index}, ${candidate.ring.family}`],
    ["NTT", candidate.modulus.ntt_condition],
    ["q - 1", candidate.modulus.q_minus_1_factorization],
    ["Split", candidate.modulus.polynomial_factorization],
    ["NTT quality", `${candidate.modulus.ntt_quality}, remaining layers ${candidate.modulus.ntt_layers_remaining}`],
    ["Secret", distributionText(candidate.distribution.secret)],
    ["Error", distributionText(candidate.distribution.error)],
  ];
  if (candidate.lwr) {
    instanceRows.push(["LWR p", String(candidate.lwr.p)]);
  }
  fillDefinitionList("#instance-list", instanceRows);

  fillDefinitionList("#security-list", [
    ["Agent", result.agent ? result.agent.name : "deterministic"],
    ["LLM", result.agent?.llm_used ? `${result.agent.provider} / ${result.agent.model}` : "not used"],
    ["Source", candidate.security.source],
    ["MATZOV", `${candidate.security.matzov_bits || "n/a"} bits`],
    ["ADPS16", `${candidate.security.adps16_core_svp_bits || "n/a"} bits`],
    ["Classical", `${candidate.security.classical_bits} bits`],
    ["Quantum", `${candidate.security.quantum_bits} bits`],
    ["Target", `${result.request.target_security} bits (${result.request.security_model})`],
    ["Reduction model", result.request.red_cost_model],
    ["Margin", `${candidate.selection.margin_bits} bits`],
    ["Estimator", candidate.security.estimator_commit || "not applied"],
    ["Next", result.next_question],
  ]);

  warnings.innerHTML = "";
  const warningItems = [...candidate.warnings, ...(result.agent?.notes || [])];
  warningItems.forEach((warning) => {
    const p = document.createElement("p");
    p.textContent = warning;
    warnings.appendChild(p);
  });

  renderAlternatives(result.alternatives);
  jsonOutput.textContent = JSON.stringify(candidate, null, 2);
}

function renderParameterProfile(candidate) {
  if (!candidate.visual_scores) {
    profilePanel.classList.add("hidden");
    return;
  }

  profilePanel.classList.remove("hidden");
  const scores = ["security", "compactness", "performance"].map((key) => {
    const score = candidate.visual_scores[key]?.score;
    return Math.max(0, Math.min(1, Number(score) || 0));
  });
  const points = trianglePoints(scores);
  document.querySelector("#profile-fill").setAttribute("points", points);
}

function trianglePoints(scores) {
  const center = [80, 70];
  const vertices = [
    [80, 24],
    [28, 94],
    [132, 94],
  ];
  return vertices
    .map(([x, y], index) => {
      const score = scores[index];
      const px = center[0] + (x - center[0]) * score;
      const py = center[1] + (y - center[1]) * score;
      return `${round1(px)},${round1(py)}`;
    })
    .join(" ");
}

function renderAlternatives(items) {
  const list = document.querySelector("#candidate-list");
  list.innerHTML = "";
  items.forEach((item) => {
    const node = document.createElement("article");
    node.className = "candidate";
    node.innerHTML = `
      <strong>n=${item.ring.n}, q=${item.modulus.q}, ${item.distribution.name}</strong>
      <span>${item.selection.security_model}: ${item.selection.selected_security_bits} bits · margin +${item.selection.margin_bits}</span>
      <span>${item.modulus.ntt_quality} · ${item.modulus.ntt_condition}</span>
    `;
    list.appendChild(node);
  });
}

function distributionText(distribution) {
  return `${distribution.name}, σ=${distribution.stddev}, support [${distribution.support.join(", ")}]`;
}

function selectedHardProblem(data = new FormData(form)) {
  const [category = "lwe", variant = "rlwe"] = String(data.get("hardProblem") || "lwe:rlwe").split(":");
  return { category, variant };
}

function syncDistributionOptions() {
  const options = DEFAULT_DISTRIBUTION_OPTIONS;
  distributionSelect.replaceChildren(
    ...options.map(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      return option;
    }),
  );
  distributionSelect.value = options[0][0];
  distributionSelect.disabled = false;
}

function formatHardProblem(request) {
  const category = request?.hard_problem_category || request?.hardProblemCategory || "lwe";
  const variant = request?.hard_problem_variant || request?.hardProblemVariant || "rlwe";
  const variantLabel = HARD_PROBLEM_VARIANT_LABELS[variant] || variant.toUpperCase();
  return `${category.toUpperCase()} / ${variantLabel}`;
}

function fillDefinitionList(selector, rows) {
  const node = document.querySelector(selector);
  node.innerHTML = "";
  rows.forEach(([key, value]) => {
    const dt = document.createElement("dt");
    dt.textContent = key;
    const dd = document.createElement("dd");
    dd.textContent = value;
    node.append(dt, dd);
  });
}

function setMeter(selector, value, target) {
  const width = Math.max(4, Math.min(100, (Number(value) / Number(target)) * 100));
  document.querySelector(selector).style.width = `${width}%`;
}

function round1(value) {
  return Math.round(Number(value) * 10) / 10;
}

function setText(selector, text) {
  document.querySelector(selector).textContent = text;
}

function show(...nodes) {
  nodes.forEach((node) => node.classList.remove("hidden"));
}

function setStatus(kind, text) {
  statusPill.className = `status-pill ${kind}`;
  statusPill.textContent = text;
}

function updateNttScaleLabel() {
  const value = Number(nttScale.value);
  const ternary = ringFamily.value === "ternary";
  if (value === 6) {
    nttScaleLabel.textContent = "NTT scale: no restriction of n and q (NTT unfriendly)";
    return;
  }
  let label = ternary ? "6n" : "2n";
  if (ternary) {
    if (value === 0) label = "3n";
    if (value === 1) label = "3n/2";
    if (value > 1) label = `3n/2^${value}`;
  } else {
    if (value === 0) label = "n";
    if (value === 1) label = "n/2";
    if (value > 1) label = `n/2^${value}`;
  }
  nttScaleLabel.textContent = `NTT scale: ${label} | q - 1`;
}

requestRecommendation();

async function loadPublicConfig() {
  try {
    const response = await fetch("/api/config/public");
    if (!response.ok) return;
    const config = await response.json();
    document.querySelector("#config-source").textContent = config.source;
    document.querySelector("#config-agent").textContent = "agent: deterministic default";
    document.querySelector("#config-llm").textContent =
      config.llm.enabled
        ? `llm: ${config.llm.configured ? "ready" : "auth missing"} · ${config.llm.provider} / ${config.llm.model}`
        : "llm: disabled";
    useLLM.disabled = !config.llm.configured;
    if (!config.llm.configured) {
      useLLM.checked = false;
    }
    const estimatorPath = config.estimator.remote_configured
      ? config.estimator.remote_url
      : config.estimator.lattice_estimator_path || "PYTHONPATH/default";
    const estimatorParts = [config.estimator.remote_configured ? "remote" : config.estimator.sage_binary];
    if (config.estimator.version) {
      estimatorParts.push(`version ${config.estimator.version}`);
    }
    if (config.estimator.remote_configured) {
      estimatorParts.push(`timeout ${config.estimator.remote_timeout_seconds}s`);
    }
    estimatorParts.push(estimatorPath);
    document.querySelector("#config-estimator").textContent = `estimator: ${estimatorParts.join(" · ")}`;
  } catch (_error) {
    return;
  }
}
