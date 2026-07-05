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

let lastResult = null;

loadPublicConfig();
updateNttScaleLabel();

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  await requestRecommendation();
});

copyJson.addEventListener("click", async () => {
  if (!lastResult) return;
  await navigator.clipboard.writeText(JSON.stringify(lastResult.recommendation, null, 2));
  copyJson.textContent = "已复制";
  setTimeout(() => {
    copyJson.textContent = "复制 JSON";
  }, 1200);
});

nttScale.addEventListener("input", updateNttScaleLabel);
ringFamily.addEventListener("change", updateNttScaleLabel);

async function requestRecommendation() {
  setStatus("loading", "Running");
  title.textContent = "正在搜索参数";
  subtitle.textContent = "生成 NTT 友好模数并筛选安全估计。";

  const data = new FormData(form);
  const payload = {
    ringFamily: data.get("ringFamily"),
    targetSecurity: Number(data.get("targetSecurity")),
    securityModel: data.get("securityModel"),
    redCostModel: data.get("redCostModel"),
    nttScalePower: Number(data.get("nttScalePower")),
    minQBits: Number(data.get("minQBits")),
    maxQBits: Number(data.get("maxQBits")),
    distribution: data.get("distribution"),
    useEstimator: data.get("useEstimator") === "on",
  };

  try {
    const response = await fetch("/api/rlwe/recommend", {
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
    title.textContent = "请求失败";
    subtitle.textContent = error.message;
  }
}

function renderResult(result) {
  const candidate = result.recommendation;
  const target = result.request.target_security;
  const source = candidate.security.source === "sage-lattice-estimator" ? "Sage estimator" : "fast screen";

  title.textContent = `推荐 RLWE(${candidate.ring.n}, ${candidate.modulus.q})`;
  subtitle.textContent = `${source} · ${result.search.generated_candidates} 个候选 · ${result.search.elapsed_ms} ms`;

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

  fillDefinitionList("#instance-list", [
    ["Family", candidate.ring.family],
    ["Ring", candidate.ring.quotient],
    ["Cyclotomic", `Φ_${candidate.ring.cyclotomic_index}, ${candidate.ring.family}`],
    ["NTT", candidate.modulus.ntt_condition],
    ["q - 1", candidate.modulus.q_minus_1_factorization],
    ["Split", candidate.modulus.polynomial_factorization],
    ["NTT quality", `${candidate.modulus.ntt_quality}, remaining layers ${candidate.modulus.ntt_layers_remaining}`],
    ["Secret", distributionText(candidate.distribution.secret)],
    ["Error", distributionText(candidate.distribution.error)],
  ]);

  fillDefinitionList("#security-list", [
    ["Source", candidate.security.source],
    ["MATZOV", `${candidate.security.matzov_bits || "n/a"} bits`],
    ["ADPS16", `${candidate.security.adps16_core_svp_bits || "n/a"} bits`],
    ["Classical", `${candidate.security.classical_bits} bits`],
    ["Quantum", `${candidate.security.quantum_bits} bits`],
    ["Target", `${result.request.target_security} bits (${result.request.red_cost_model})`],
    ["Margin", `${candidate.selection.margin_bits} bits`],
    ["Estimator", candidate.security.estimator_commit || "not applied"],
    ["Next", result.next_question],
  ]);

  warnings.innerHTML = "";
  candidate.warnings.forEach((warning) => {
    const p = document.createElement("p");
    p.textContent = warning;
    warnings.appendChild(p);
  });

  renderAlternatives(result.alternatives);
  jsonOutput.textContent = JSON.stringify(candidate, null, 2);
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
  nttScaleLabel.textContent = `NTT 尺度：${label} | q - 1`;
}

requestRecommendation();

async function loadPublicConfig() {
  try {
    const response = await fetch("/api/config/public");
    if (!response.ok) return;
    const config = await response.json();
    document.querySelector("#config-source").textContent = config.source;
    document.querySelector("#config-model").textContent = `model: ${config.model.provider} / ${config.model.model}`;
    const estimatorPath = config.estimator.lattice_estimator_path || "PYTHONPATH/default";
    document.querySelector("#config-estimator").textContent = `estimator: ${config.estimator.sage_binary}, ${estimatorPath}`;
  } catch (_error) {
    return;
  }
}
