const EasyLatticeModel = window.EasyLatticeModel;
const form = document.querySelector("#parameter-form");
const statusPill = document.querySelector("#status-pill");
const title = document.querySelector("#summary-title");
const subtitle = document.querySelector("#summary-subtitle");
const resultGrid = document.querySelector("#result-grid");
const details = document.querySelector("#details");
const warnings = document.querySelector("#warnings");
const alternatives = document.querySelector("#alternatives");
const copyJson = document.querySelector("#copy-json");
const dfrForm = document.querySelector("#dfr-form");
const dfrResults = document.querySelector("#dfr-results");
const dfrDistributionEditors = document.querySelector("#dfr-distribution-editors");
const copyDfrJson = document.querySelector("#copy-dfr-json");
const searchResults = document.querySelector("#search-results");
const parameterForm = document.querySelector("#parameter-form");
const dfrNtruFields = document.querySelector("#dfr-ntru-fields");
const dfrLweFields = document.querySelector("#dfr-lwe-fields");
const nttScale = document.querySelector("#ntt-scale");
const nttScaleLabel = document.querySelector("#ntt-scale-label");
const ringFamily = document.querySelector("#ring-family");
const secretDistributionSelect = document.querySelector("#secret-distribution");
const errorDistributionSelect = document.querySelector("#error-distribution");
const errorDistributionLabel = document.querySelector("#error-distribution-label");
const languageSelect = document.querySelector("#language-select");
const useLLM = document.querySelector("#use-llm");
const profilePanel = document.querySelector("#profile-panel");
const searchSubmit = form.querySelector('button[type="submit"]');
const dfrSubmit = dfrForm.querySelector('button[type="submit"]');

const searchState = EasyLatticeModel.createRequestState();
const dfrState = EasyLatticeModel.createRequestState();
let publicConfig = null;
let currentLanguage = supportedLanguage(localStorage.getItem("easyLatticeLanguage") || navigator.language || "en");
let activeWorkspace = "search";
let renderedDfrType = null;
const PREVIEW_MODE = new URLSearchParams(window.location.search).get("preview") === "1"
  && Boolean(window.EASYLATTICE_PREVIEW_FIXTURES);
const DEFAULT_DISTRIBUTION_OPTIONS = [
  ["auto", "distributionAuto"],
  ["centered_binomial", "centeredBinomial"],
  ["sparse_ternary", "sparseTernary"],
];
const LWR_COMPRESSION_OPTIONS = [
  ["2", "2"],
  ["3", "3"],
  ["4", "4"],
  ["5", "5"],
  ["8", "8"],
  ["16", "16"],
  ["32", "32"],
  ["64", "64"],
  ["128", "128"],
  ["256", "256"],
  ["512", "512"],
  ["1024", "1024"],
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
const LWR_VARIANTS = new Set(["lwr", "rlwr", "mlwr"]);
const DFR_DISTRIBUTION_OPTIONS = [
  ["centered_binomial", "centeredBinomial"],
  ["discrete_gaussian", "discreteGaussian"],
  ["uniform", "uniformDistribution"],
  ["uniform_mod", "uniformMod"],
  ["t_uniform", "tUniform"],
  ["sparse_ternary", "sparseTernary"],
  ["sparse_binary", "sparseBinary"],
  ["binary", "binaryDistribution"],
  ["ternary", "ternaryDistribution"],
  ["lwr_floor_compression", "lwrFloorCompression"],
  ["kyber_nearest_compression", "kyberNearestCompression"],
  ["custom_pmf", "customPmf"],
];
const DFR_FIELDS = {
  ntru: ["g", "f", "s", "e", "m"],
  lwe: ["s", "e", "e1", "r", "e2", "ec1", "ec2"],
};
const DFR_DEFAULTS = {
  ntru: {
    g: { type: "centered_binomial", params: { eta: "1" } },
    f: { type: "centered_binomial", params: { eta: "1" } },
    s: { type: "centered_binomial", params: { eta: "1" } },
    e: { type: "centered_binomial", params: { eta: "1" } },
    m: { type: "centered_binomial", params: { eta: "1" } },
  },
  lwe: {
    s: { type: "centered_binomial", params: { eta: "3" } },
    e: { type: "centered_binomial", params: { eta: "3" } },
    e1: { type: "centered_binomial", params: { eta: "2" } },
    r: { type: "centered_binomial", params: { eta: "3" } },
    e2: { type: "centered_binomial", params: { eta: "2" } },
    ec1: { type: "kyber_nearest_compression", params: { q: "3329", d: "10" } },
    ec2: { type: "kyber_nearest_compression", params: { q: "3329", d: "4" } },
  },
};
const TRANSLATIONS = {
  en: {
    brandSubtitle: "Local lattice parameter assistant",
    localConfiguration: "Local configuration",
    defaults: "defaults",
    underlyingHardProblem: "Underlying hard problem",
    polynomialForm: "Polynomial form",
    targetSecurityBits: "Target security bits",
    securityMetric: "Security metric",
    classical: "Classical",
    quantum: "Quantum",
    reductionCostModel: "Reduction cost model",
    nttUnfriendly: "NTT scale: no restriction of n and q (NTT unfriendly)",
    nttScale: "NTT scale: {label} | q - 1",
    minimumModulusBits: "Minimum modulus bits",
    maximumModulusBits: "Maximum modulus bits",
    secretDistribution: "Secret distribution",
    errorDistribution: "Error distribution",
    compressionModulusP: "Compression modulus p",
    refineWithSage: "Refine with Sage estimator",
    naturalLanguageConstraints: "Natural-language constraints",
    intentPlaceholder: "Example: 128-bit MATZOV, ternary cyclotomic ring, prefer n/2 NTT scale, smallest possible modulus",
    useConfiguredLlm: "Use the locally configured LLM to parse constraints",
    generateRecommendation: "Generate recommendation",
    waitingForInput: "Waiting for input",
    chooseTarget: "Choose a target to generate a lattice instance.",
    statusIdle: "Idle",
    statusRunning: "Running",
    statusReady: "Ready",
    statusError: "Error",
    statusInputsChanged: "Inputs changed",
    searchingParameters: "Searching parameters",
    generatingSubtitle: "Generating NTT-friendly moduli and screening security estimates.",
    estimatorSubtitle: "Running lattice-estimator validation. This can take several minutes.",
    estimatorWaiting: "Estimator job {status}. Waiting for Sage/lattice-estimator.",
    requestFailed: "Request failed",
    recommendedInstance: "Recommended lattice instance ({n}, {q})",
    summaryStats: "{source} · {count} candidates · {ms} ms",
    sageEstimator: "Sage estimator",
    fastScreen: "fast screen",
    bits: "{value} bits",
    margin: "margin +{value} bits",
    classicalSecurity: "Classical security",
    quantumSecurity: "Quantum security",
    ringDimension: "Ring dimension",
    modulus: "Modulus",
    selectedMetric: "Selected metric",
    parameterProfile: "Parameter profile",
    security: "Security",
    compactness: "Compactness",
    efficiency: "Efficiency",
    instance: "Instance",
    estimate: "Estimate",
    alternativeCandidates: "Alternative candidates",
    copyJson: "Copy JSON",
    copied: "Copied",
    hardProblem: "Hard problem",
    family: "Family",
    ring: "Ring",
    cyclotomic: "Cyclotomic",
    ntt: "NTT",
    qMinusOne: "q - 1",
    split: "Split",
    nttQuality: "NTT quality",
    secret: "Secret",
    error: "Error",
    lwrP: "LWR p",
    agent: "Agent",
    llm: "LLM",
    source: "Source",
    target: "Target",
    reductionModel: "Reduction model",
    securityLevel: "Security level",
    marginLabel: "Margin",
    estimator: "Estimator",
    next: "Next",
    notUsed: "not used",
    notApplied: "not applied",
    notAvailable: "n/a",
    requiresSageEstimate: "requires Sage estimate",
    distributionAuto: "Auto",
    centeredBinomial: "Centered Binomial",
    sparseTernary: "Sparse Ternary",
    compressionP: "p={value}",
    distributionText: "{name}, sigma={stddev}, support [{support}]",
    alternativeSummary: "{model}: {bits} bits · margin +{margin}",
    configAgent: "agent: deterministic default",
    configLlmReady: "llm: ready · {provider} / {model}",
    configLlmAuthMissing: "llm: auth missing · {provider} / {model}",
    configLlmDisabled: "llm: disabled",
    configEstimator: "estimator: {parts}",
    workspace: "Workspace",
    parameterSearch: "Parameter search",
    decryptionFailure: "Decryption failure",
    decryptionFailureType: "Calculator type",
    dfrRingType: "Polynomial ring",
    ringDimensionN: "Ring dimension n",
    productDimensionM: "Product dimension m",
    outputDimensionN: "Output dimension n",
    workingPrecision: "Working precision",
    tailBits: "Gaussian tail bits",
    distributions: "Distributions",
    calculateDfr: "Calculate DFR",
    calculatingDfr: "Calculating decryption failure rate",
    dfrCalculatingSubtitle: "Building finite distributions and convolving the requested error terms.",
    dfrResultTitle: "{type} decryption failure rate",
    dfrFormula: "Formula: {formula}",
    dfrFailed: "Decryption failure calculation failed",
    singleCoefficientDfr: "log2 single-coefficient DFR",
    vectorDfr: "log2 vector DFR before ECC",
    distributionSupport: "Error support",
    calculation: "Calculation",
    formula: "Formula",
    successCondition: "Success condition",
    delta: "Delta",
    precision: "Precision",
    tailBound: "Tail bound",
    vectorAggregation: "Vector aggregation",
    unionBound: "Union bound",
    dfrLog2: "log2 = {value}",
    supportRange: "[{minimum}, {maximum}]",
    eta: "eta",
    standardDeviation: "Standard deviation",
    mean: "Mean",
    lowerBound: "Lower bound",
    upperBound: "Upper bound",
    modulusQ: "Modulus q",
    exponentB: "Exponent b",
    plusWeight: "+1 weight",
    minusWeight: "-1 weight",
    weight: "Weight",
    dimension: "Distribution dimension",
    compressionModulus: "Compression modulus p",
    compressionBits: "Compression bits d",
    customPmf: "Custom PMF",
    customPmfPlaceholder: "{\"-1\": \"0.25\", \"0\": \"0.5\", \"1\": \"0.25\"}",
    invalidCustomPmf: "{name} custom PMF must be valid JSON.",
    discreteGaussian: "Discrete Gaussian",
    uniformDistribution: "Uniform",
    uniformMod: "Uniform mod q",
    tUniform: "TUniform",
    sparseBinary: "Sparse Binary",
    binaryDistribution: "Binary",
    ternaryDistribution: "Ternary",
    lwrFloorCompression: "LWR floor compression",
    kyberNearestCompression: "Kyber nearest compression",
    beforeEcc: "before ECC",
    dfrWarningUnionBound: "Vector DFR uses a union bound and does not assume independent output coefficients.",
    dfrWarningTail: "Reported probabilities exclude bounded discrete-Gaussian tails.",
    dfrWarningSparse: "Sparse ternary uses its single-coefficient marginal and ignores fixed-weight correlation.",
    dfrEccExternal: "Apply a scheme-specific error-correction calculation outside this module.",
  },
  zh: {
    brandSubtitle: "本地格密码参数助手",
    localConfiguration: "本地配置",
    defaults: "默认配置",
    underlyingHardProblem: "底层困难问题",
    polynomialForm: "多项式形式",
    targetSecurityBits: "目标安全比特",
    securityMetric: "安全度量",
    classical: "经典",
    quantum: "量子",
    reductionCostModel: "规约代价模型",
    nttUnfriendly: "NTT 规模：不限制 n 和 q（不利于 NTT）",
    nttScale: "NTT 规模：{label} | q - 1",
    minimumModulusBits: "最小模数比特",
    maximumModulusBits: "最大模数比特",
    secretDistribution: "Secret 分布",
    errorDistribution: "Error 分布",
    compressionModulusP: "压缩模数 p",
    refineWithSage: "使用 Sage estimator 细化",
    naturalLanguageConstraints: "自然语言约束",
    intentPlaceholder: "示例：128-bit MATZOV，三元分圆环，偏好 n/2 NTT 规模，尽量小的模数",
    useConfiguredLlm: "使用本地配置的 LLM 解析约束",
    generateRecommendation: "生成推荐",
    waitingForInput: "等待输入",
    chooseTarget: "选择目标后生成格实例。",
    statusIdle: "空闲",
    statusRunning: "运行中",
    statusReady: "就绪",
    statusError: "错误",
    statusInputsChanged: "输入已更改",
    searchingParameters: "正在搜索参数",
    generatingSubtitle: "正在生成适合 NTT 的模数并筛选安全估计。",
    estimatorSubtitle: "正在运行 lattice-estimator 验证，可能需要几分钟。",
    estimatorWaiting: "Estimator 任务 {status}，等待 Sage/lattice-estimator。",
    requestFailed: "请求失败",
    recommendedInstance: "推荐格实例 ({n}, {q})",
    summaryStats: "{source} · {count} 个候选 · {ms} ms",
    sageEstimator: "Sage estimator",
    fastScreen: "快速筛选",
    bits: "{value} bits",
    margin: "余量 +{value} bits",
    classicalSecurity: "经典安全",
    quantumSecurity: "量子安全",
    ringDimension: "环维度",
    modulus: "模数",
    selectedMetric: "选定度量",
    parameterProfile: "参数画像",
    security: "安全",
    compactness: "紧凑",
    efficiency: "效率",
    instance: "实例",
    estimate: "估计",
    alternativeCandidates: "备选候选",
    copyJson: "复制 JSON",
    copied: "已复制",
    hardProblem: "困难问题",
    family: "族",
    ring: "环",
    cyclotomic: "分圆",
    ntt: "NTT",
    qMinusOne: "q - 1",
    split: "分解",
    nttQuality: "NTT 质量",
    secret: "Secret",
    error: "Error",
    lwrP: "LWR p",
    agent: "Agent",
    llm: "LLM",
    source: "来源",
    target: "目标",
    reductionModel: "规约模型",
    securityLevel: "安全级别",
    marginLabel: "余量",
    estimator: "Estimator",
    next: "下一步",
    notUsed: "未使用",
    notApplied: "未应用",
    notAvailable: "无",
    requiresSageEstimate: "需 Sage 评估",
    distributionAuto: "自动",
    centeredBinomial: "中心二项分布",
    sparseTernary: "稀疏三元分布",
    compressionP: "p={value}",
    distributionText: "{name}, sigma={stddev}, support [{support}]",
    alternativeSummary: "{model}: {bits} bits · 余量 +{margin}",
    configAgent: "agent：确定性默认模式",
    configLlmReady: "llm：已就绪 · {provider} / {model}",
    configLlmAuthMissing: "llm：缺少认证 · {provider} / {model}",
    configLlmDisabled: "llm：已禁用",
    configEstimator: "estimator：{parts}",
    workspace: "工作区",
    parameterSearch: "参数搜索",
    decryptionFailure: "解密错误率",
    decryptionFailureType: "计算器类型",
    dfrRingType: "多项式环",
    ringDimensionN: "环维度 n",
    productDimensionM: "乘积维度 m",
    outputDimensionN: "输出维度 n",
    workingPrecision: "工作精度",
    tailBits: "高斯尾界比特",
    distributions: "分布",
    calculateDfr: "计算 DFR",
    calculatingDfr: "正在计算解密错误率",
    dfrCalculatingSubtitle: "正在构造有限分布并卷积所选误差项。",
    dfrResultTitle: "{type} 解密错误率",
    dfrFormula: "公式：{formula}",
    dfrFailed: "解密错误率计算失败",
    singleCoefficientDfr: "单系数 log2 DFR",
    vectorDfr: "纠错前向量 log2 DFR",
    distributionSupport: "误差支持集",
    calculation: "计算",
    formula: "公式",
    successCondition: "成功条件",
    delta: "Delta",
    precision: "精度",
    tailBound: "尾界",
    vectorAggregation: "向量聚合",
    unionBound: "Union bound",
    dfrLog2: "log2 = {value}",
    supportRange: "[{minimum}, {maximum}]",
    eta: "eta",
    standardDeviation: "标准差",
    mean: "均值",
    lowerBound: "下界",
    upperBound: "上界",
    modulusQ: "模数 q",
    exponentB: "指数 b",
    plusWeight: "+1 权重",
    minusWeight: "-1 权重",
    weight: "权重",
    dimension: "分布维度",
    compressionModulus: "压缩模数 p",
    compressionBits: "压缩比特 d",
    customPmf: "自定义 PMF",
    customPmfPlaceholder: "{\"-1\": \"0.25\", \"0\": \"0.5\", \"1\": \"0.25\"}",
    invalidCustomPmf: "{name} 的自定义 PMF 必须是有效 JSON。",
    discreteGaussian: "离散高斯分布",
    uniformDistribution: "均匀分布",
    uniformMod: "模 q 均匀分布",
    tUniform: "TUniform",
    sparseBinary: "稀疏二元分布",
    binaryDistribution: "二元分布",
    ternaryDistribution: "三元分布",
    lwrFloorCompression: "LWR 向下取整压缩",
    kyberNearestCompression: "Kyber 最近整数压缩",
    beforeEcc: "纠错前",
    dfrWarningUnionBound: "向量 DFR 使用 union bound，不假定输出系数相互独立。",
    dfrWarningTail: "报告的概率不包含已界定的离散高斯尾部。",
    dfrWarningSparse: "稀疏三元分布使用单系数边缘分布，并忽略固定权重相关性。",
    dfrEccExternal: "请在此模块外使用具体方案的纠错概率计算。",
  },
};

languageSelect.value = currentLanguage;
applyLanguage();
syncRingControls();
syncDistributionOptions();
updateNttScaleLabel();
renderDfrDistributionEditors();
syncDfrForm();
syncWorkspace();
updateRequestControls();

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (searchState.snapshot().inFlight || (!hasLiveApi() && !PREVIEW_MODE)) return;
  await requestRecommendation();
});

dfrForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (dfrState.snapshot().inFlight || (!hasLiveApi() && !PREVIEW_MODE)) return;
  await requestDfr();
});

copyJson.addEventListener("click", async () => {
  const state = searchState.snapshot();
  if (!state.copyEligible) return;
  await navigator.clipboard.writeText(JSON.stringify(state.result.recommendation, null, 2));
  copyJson.textContent = t("copied");
  setTimeout(() => {
    copyJson.textContent = t("copyJson");
  }, 1200);
});

copyDfrJson.addEventListener("click", async () => {
  const state = dfrState.snapshot();
  if (!state.copyEligible) return;
  await navigator.clipboard.writeText(JSON.stringify(state.result, null, 2));
  copyDfrJson.textContent = t("copied");
  setTimeout(() => {
    copyDfrJson.textContent = t("copyJson");
  }, 1200);
});

nttScale.addEventListener("input", updateNttScaleLabel);
ringFamily.addEventListener("change", () => {
  syncRingControls();
  updateNttScaleLabel();
});
languageSelect.addEventListener("change", () => {
  currentLanguage = supportedLanguage(languageSelect.value);
  localStorage.setItem("easyLatticeLanguage", currentLanguage);
  applyLanguage();
  syncRingControls();
  syncDistributionOptions();
  updateNttScaleLabel();
  renderDfrDistributionEditors();
  syncDfrForm();
  if (publicConfig) renderPublicConfig(publicConfig);
  syncWorkspace();
});
document.querySelectorAll('input[name="hardProblem"]').forEach((input) => {
  input.addEventListener("change", () => {
    syncRingControls();
    syncDistributionOptions();
    updateNttScaleLabel();
  });
});
document.querySelectorAll('input[name="workspaceMode"]').forEach((input) => {
  input.addEventListener("change", syncWorkspace);
});
document.querySelectorAll('input[name="dfrType"]').forEach((input) => {
  input.addEventListener("change", syncDfrForm);
});
dfrDistributionEditors.addEventListener("change", (event) => {
  if (event.target.matches("[data-dfr-distribution-type]")) {
    renderDfrDistributionEditors();
  }
});
form.addEventListener("input", markSearchInputsChanged);
dfrForm.addEventListener("input", markDfrInputsChanged);

async function requestRecommendation() {
  if (searchState.snapshot().inFlight) return;
  const data = new FormData(form);
  const useEstimator = data.get("useEstimator") === "on";
  const request = searchState.begin({
    subtitleKey: useEstimator ? "estimatorSubtitle" : "generatingSubtitle",
    subtitleValues: {},
  });
  updateRequestControls();
  if (activeWorkspace === "search") renderSearchState();

  try {
    const hardProblem = selectedHardProblem(data);
    const ringSelection = EasyLatticeModel.normalizeRingSelection(
      hardProblem.category,
      String(data.get("ringFamily") || "power2"),
      hardProblem.variant,
    );
    const secretDistribution = data.get("secretDistribution");
    const errorDistribution = data.get("errorDistribution");
    const payload = {
      problem: hardProblem.category === "ntru" ? "ntru" : "rlwe",
      hardProblemCategory: hardProblem.category,
      hardProblemVariant: ringSelection.variant,
      ringFamily: ringSelection.family,
      targetSecurity: Number(data.get("targetSecurity")),
      securityModel: data.get("securityModel"),
      redCostModel: data.get("redCostModel"),
      nttScalePower: Number(data.get("nttScalePower")),
      minQBits: Number(data.get("minQBits")),
      maxQBits: Number(data.get("maxQBits")),
      distribution: secretDistribution,
      secretDistribution,
      errorDistribution,
      useEstimator,
      estimatorTimeout: useEstimator ? 240 : undefined,
      intent: String(data.get("intent") || ""),
      useLLM: data.get("useLLM") === "on",
    };

    const result = PREVIEW_MODE
      ? previewRecommendation(payload)
      : useEstimator
        ? await requestRecommendationJob(payload, request)
        : await postJson("/api/agent/recommend", payload);
    searchState.acceptResult(request, result);
  } catch (error) {
    searchState.acceptError(request, {
      titleKey: "requestFailed",
      message: error.message,
    });
  } finally {
    if (searchState.finish(request)) {
      updateRequestControls();
      if (activeWorkspace === "search") renderSearchState();
    }
  }
}

async function requestDfr() {
  if (dfrState.snapshot().inFlight) return;
  const request = dfrState.begin();
  updateRequestControls();
  if (activeWorkspace === "dfr") renderDfrState();

  try {
    const result = PREVIEW_MODE
      ? previewDfrResult(selectedDfrType())
      : await postJson("/api/decryption-failure/calculate", buildDfrPayload());
    dfrState.acceptResult(request, result);
  } catch (error) {
    dfrState.acceptError(request, {
      titleKey: "dfrFailed",
      message: error.message,
    });
  } finally {
    if (dfrState.finish(request)) {
      updateRequestControls();
      if (activeWorkspace === "dfr") renderDfrState();
    }
  }
}

function renderDfrResult(result) {
  title.textContent = t("dfrResultTitle", { type: String(result.type || "").toUpperCase() });
  subtitle.textContent = t("dfrFormula", { formula: result.formula });
  dfrResults.classList.remove("hidden");

  setText("#dfr-single", formatLog2(result.single_coefficient_dfr_log2));
  setText("#dfr-vector", formatLog2(result.vector_dfr_log2_before_ecc));
  setText("#dfr-precision", `${result.precision_bits} bits`);
  setText("#dfr-tail", `${t("tailBound")}: ${formatProbability(result.tail_probability_upper_bound)}`);
  setText("#dfr-support", String(result.error_support.size));
  setText("#dfr-support-range", t("supportRange", {
    minimum: formatProbability(result.error_support.minimum),
    maximum: formatProbability(result.error_support.maximum),
  }));

  const dimensionRows = Object.entries(result.dimensions || []).map(([key, value]) => [key, String(value)]);
  fillDefinitionList("#dfr-calculation-list", [
    [t("singleCoefficientDfr"), formatLog2(result.single_coefficient_dfr_log2)],
    [t("vectorDfr"), formatLog2(result.vector_dfr_log2_before_ecc)],
    [t("formula"), result.formula],
    [t("successCondition"), result.success_condition],
    [t("delta"), result.delta],
    [t("vectorAggregation"), t("unionBound")],
    ...dimensionRows,
    [t("precision"), `${result.precision_bits} bits (${result.precision_decimal_digits} decimal digits)`],
    [t("tailBound"), result.tail_probability_upper_bound],
  ]);
  fillDefinitionList("#dfr-distribution-list", Object.entries(result.distributions || {}).map(([name, summary]) => [
    name,
    `${summary.support_size} · ${t("supportRange", {
      minimum: formatProbability(summary.support[0]),
      maximum: formatProbability(summary.support[1]),
    })}`,
  ]));

  const dfrWarnings = document.querySelector("#dfr-warnings");
  dfrWarnings.innerHTML = "";
  const warningItems = [...(result.warnings || []), result.error_correction?.note]
    .filter(Boolean)
    .map(localizeDfrWarning);
  warningItems.forEach((warning) => {
    const paragraph = document.createElement("p");
    paragraph.textContent = warning;
    dfrWarnings.appendChild(paragraph);
  });
  dfrWarnings.classList.toggle("hidden", warningItems.length === 0);
}

function formatProbability(value) {
  const raw = String(value ?? "-");
  if (raw === "0" || raw === "-Infinity") return raw;
  const match = raw.match(/^([+-]?[0-9.]+)E([+-]?\d+)$/i);
  if (!match) return raw;
  const coefficient = Number(match[1]);
  if (!Number.isFinite(coefficient)) return raw;
  return `${coefficient.toPrecision(6).replace(/\.?(0+)$/, "")}e${Number(match[2])}`;
}

function formatLog2(value) {
  const raw = String(value ?? "-");
  const numeric = Number(raw);
  return Number.isFinite(numeric) ? numeric.toFixed(2) : raw;
}

function localizeDfrWarning(warning) {
  const translations = {
    "Vector DFR uses a union bound and does not assume independent output coefficients.": "dfrWarningUnionBound",
    "Reported probabilities exclude bounded discrete-Gaussian tails.": "dfrWarningTail",
    "Sparse ternary uses its single-coefficient marginal and ignores fixed-weight correlation.": "dfrWarningSparse",
    "Apply a scheme-specific error-correction calculation outside this module.": "dfrEccExternal",
  };
  return translations[warning] ? t(translations[warning]) : warning;
}

function setDfrIdleHeading() {
  title.textContent = t("decryptionFailure");
  subtitle.textContent = t("dfrCalculatingSubtitle");
  setStatus("idle", t("statusIdle"));
}

async function requestRecommendationJob(payload, request) {
  const submitted = await postJson("/api/agent/jobs", payload, { accepted: true });
  if (!searchState.accepts(request)) {
    throw new Error("search inputs changed while the estimator was running");
  }
  const jobId = submitted.job_id;
  if (!jobId) {
    throw new Error("estimator job did not return an id");
  }

  const timeoutMs = (Number(payload.estimatorTimeout) || 240) * 1000 + 30000;
  const deadline = Date.now() + timeoutMs;
  let job = submitted;
  while (Date.now() < deadline) {
    if (!searchState.accepts(request)) {
      throw new Error("search inputs changed while the estimator was running");
    }
    if (job.status === "succeeded") {
      if (!job.result) throw new Error("estimator job completed without a result");
      return job.result;
    }
    if (job.status === "failed") {
      throw new Error(job.error || "estimator job failed");
    }
    if (searchState.update(request, {
      subtitleKey: "estimatorWaiting",
      subtitleValues: { status: job.status },
    }) && activeWorkspace === "search") {
      renderSearchState();
    }
    await sleep(2000);
    job = await getJson(`/api/agent/jobs/${jobId}`);
  }
  throw new Error("estimator job polling timed out");
}

async function postJson(path, payload, options = {}) {
  const response = await fetch(apiUrl(path), {
    method: "POST",
    headers: apiHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok && !(options.accepted && response.status === 202)) {
    throw new Error(result.error || "request failed");
  }
  return result;
}

async function getJson(path) {
  const response = await fetch(apiUrl(path), { headers: apiHeaders() });
  const result = await response.json();
  if (!response.ok) {
    throw new Error(result.error || "request failed");
  }
  return result;
}

function renderResult(result) {
  const candidate = result.recommendation;
  const target = result.request.target_security;
  const redCostModel = result.request.red_cost_model || result.request.redCostModel || "matzov";
  const displayedSecurity = securityBitsForReductionModel(candidate.security, redCostModel);
  const source = String(candidate.security.source || "").startsWith("sage-lattice-estimator")
    ? t("sageEstimator")
    : t("fastScreen");

  title.textContent = t("recommendedInstance", { n: candidate.ring.n, q: candidate.modulus.q });
  subtitle.textContent = t("summaryStats", {
    source,
    count: result.search.generated_candidates,
    ms: result.search.elapsed_ms,
  });

  show(resultGrid, details, warnings, alternatives);
  setText("#classic-bits", formatBits(displayedSecurity.classical));
  setText("#quantum-bits", formatBits(displayedSecurity.quantum));
  setText("#ring-n", String(candidate.ring.n));
  setText("#ring-poly", candidate.ring.polynomial);
  setText("#modulus-q", String(candidate.modulus.q));
  setText("#modulus-bits", formatBits(candidate.modulus.bits));
  setText("#selected-bits", formatBits(candidate.selection.selected_security_bits));
  setText("#security-level", candidate.selection.security_level || t("notAvailable"));
  setText("#security-margin", t("margin", { value: candidate.selection.margin_bits }));
  setMeter("#classic-meter", displayedSecurity.classical, target);
  setMeter("#quantum-meter", displayedSecurity.quantum, target);
  renderParameterProfile(candidate);

  const instanceRows = [
    [t("hardProblem"), formatHardProblem(result.request)],
    [t("family"), candidate.ring.family],
    [t("ring"), candidate.ring.quotient],
    [t("cyclotomic"), `Φ_${candidate.ring.cyclotomic_index}, ${candidate.ring.family}`],
    [t("ntt"), candidate.modulus.ntt_condition],
    [t("qMinusOne"), candidate.modulus.q_minus_1_factorization],
    [t("split"), candidate.modulus.polynomial_factorization],
    [t("nttQuality"), `${candidate.modulus.ntt_quality}, remaining layers ${candidate.modulus.ntt_layers_remaining}`],
    [t("secret"), distributionText(candidate.distribution.secret)],
    [t("error"), distributionText(candidate.distribution.error)],
  ];
  if (candidate.lwr) {
    instanceRows.push([t("lwrP"), String(candidate.lwr.p)]);
  }
  fillDefinitionList("#instance-list", instanceRows);

  fillDefinitionList("#security-list", [
    [t("agent"), result.agent ? result.agent.name : "deterministic"],
    [t("llm"), result.agent?.llm_used ? `${result.agent.provider} / ${result.agent.model}` : t("notUsed")],
    [t("source"), candidate.security.source],
    ["MATZOV (classical)", formatBits(candidate.security.matzov_bits)],
    ["MATZOV (quantum)", formatBits(candidate.security.matzov_quantum_bits)],
    ["ADPS16 (classical)", formatBits(candidate.security.adps16_core_svp_bits)],
    ["ADPS16 (quantum)", formatBits(candidate.security.adps16_quantum_bits)],
    [t("classical"), formatBits(displayedSecurity.classical)],
    [t("quantum"), formatBits(displayedSecurity.quantum)],
    [t("target"), `${result.request.target_security} bits (${result.request.security_model})`],
    [t("reductionModel"), redCostModel],
    [t("securityLevel"), candidate.selection.security_level || t("notAvailable")],
    [t("marginLabel"), formatBits(candidate.selection.margin_bits)],
    [t("estimator"), candidate.security.estimator_commit || t("notApplied")],
    [t("next"), result.next_question],
  ]);

  warnings.innerHTML = "";
  const warningItems = [...candidate.warnings, ...(result.agent?.notes || [])];
  warningItems.forEach((warning) => {
    const p = document.createElement("p");
    p.textContent = warning;
    warnings.appendChild(p);
  });

  renderAlternatives(result.alternatives);
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
      <span>${t("alternativeSummary", {
        model: item.selection.security_model,
        bits: item.selection.selected_security_bits,
        margin: item.selection.margin_bits,
      })}</span>
      <span>${item.modulus.ntt_quality} · ${item.modulus.ntt_condition}</span>
    `;
    list.appendChild(node);
  });
}

function distributionText(distribution) {
  return t("distributionText", {
    name: distribution.name,
    stddev: distribution.stddev,
    support: distribution.support.join(", "),
  });
}

function formatBits(value) {
  if (value === null || value === undefined || value === "") {
    return t("requiresSageEstimate");
  }
  return t("bits", { value });
}

function securityBitsForReductionModel(security, redCostModel) {
  if (redCostModel === "matzov") {
    return {
      classical: security.matzov_bits ?? security.classical_bits,
      quantum: security.matzov_quantum_bits ?? security.quantum_bits,
    };
  }
  if (redCostModel === "adps16") {
    return {
      classical: security.adps16_core_svp_bits ?? security.classical_bits,
      quantum: security.adps16_quantum_bits ?? security.quantum_bits,
    };
  }
  return { classical: security.classical_bits, quantum: security.quantum_bits };
}

function selectedHardProblem(data = new FormData(form)) {
  const checkedValue = document.querySelector('input[name="hardProblem"]:checked')?.value;
  const [category = "lwe", variant = "rlwe"] = String(
    data.get("hardProblem") || checkedValue || "lwe:rlwe",
  ).split(":");
  return { category, variant };
}

function syncRingControls() {
  const hardProblem = selectedHardProblem();
  const options = EasyLatticeModel.ringOptions(hardProblem.category);
  const previousFamily = ringFamily.value;
  ringFamily.replaceChildren(
    ...options.map(({ value, label }) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      return option;
    }),
  );
  ringFamily.value = options.some(({ value }) => value === previousFamily)
    ? previousFamily
    : options[0].value;

  const normalized = EasyLatticeModel.normalizeRingSelection(
    hardProblem.category,
    ringFamily.value,
    hardProblem.variant,
  );
  const matrixInput = document.querySelector('input[name="hardProblem"][value="ntru:matrix"]');
  const ringInput = document.querySelector('input[name="hardProblem"][value="ntru:ring"]');
  matrixInput.disabled = hardProblem.category === "ntru" && !normalized.matrixAllowed;
  if (hardProblem.category === "ntru" && normalized.variant === "ring") {
    ringInput.checked = true;
  }
}

function updateRequestControls() {
  const search = searchState.snapshot();
  const dfr = dfrState.snapshot();
  searchSubmit.disabled = search.inFlight;
  dfrSubmit.disabled = dfr.inFlight;
  copyJson.disabled = !search.copyEligible;
  copyDfrJson.disabled = !dfr.copyEligible;
}

function markSearchInputsChanged() {
  searchState.invalidate();
  updateRequestControls();
  if (activeWorkspace === "search") renderSearchState();
}

function markDfrInputsChanged() {
  dfrState.invalidate();
  updateRequestControls();
  if (activeWorkspace === "dfr") renderDfrState();
}

function syncDistributionOptions() {
  const hardProblem = selectedHardProblem();
  fillSelect(secretDistributionSelect, DEFAULT_DISTRIBUTION_OPTIONS, "auto");
  if (LWR_VARIANTS.has(hardProblem.variant)) {
    errorDistributionLabel.textContent = t("compressionModulusP");
    fillSelect(errorDistributionSelect, LWR_COMPRESSION_OPTIONS, "3", formatCompressionOption);
    return;
  }
  errorDistributionLabel.textContent = t("errorDistribution");
  fillSelect(errorDistributionSelect, DEFAULT_DISTRIBUTION_OPTIONS, "auto");
}

function syncWorkspace() {
  activeWorkspace = document.querySelector('input[name="workspaceMode"]:checked')?.value || "search";
  const dfrActive = activeWorkspace === "dfr";
  const dfr = dfrState.snapshot();
  parameterForm.classList.toggle("hidden", dfrActive);
  dfrForm.classList.toggle("hidden", !dfrActive);
  searchResults.classList.toggle("hidden", dfrActive);
  dfrResults.classList.toggle("hidden", !dfrActive || !dfr.result);
  updateRequestControls();

  if (dfrActive) {
    renderDfrState();
    return;
  }
  renderSearchState();
}

function renderSearchState() {
  const state = searchState.snapshot();
  if (state.result) renderResult(state.result);
  if (state.inFlight) {
    const metadata = state.metadata || {};
    title.textContent = t("searchingParameters");
    subtitle.textContent = t(metadata.subtitleKey || "generatingSubtitle", metadata.subtitleValues || {});
    setStatus("loading", t("statusRunning"));
    return;
  }
  if (state.error) {
    title.textContent = t(state.error.titleKey);
    subtitle.textContent = state.error.message;
    setStatus("error", t("statusError"));
    return;
  }
  if (state.resultCurrent) {
    setStatus("done", t("statusReady"));
    return;
  }
  if (state.stale) {
    if (!state.result) {
      title.textContent = t("waitingForInput");
      subtitle.textContent = t("chooseTarget");
    }
    setStatus("warning", t("statusInputsChanged"));
    return;
  }
  title.textContent = t("waitingForInput");
  subtitle.textContent = t("chooseTarget");
  setStatus("idle", t("statusIdle"));
}

function renderDfrState() {
  const state = dfrState.snapshot();
  if (state.result) renderDfrResult(state.result);
  if (state.inFlight) {
    title.textContent = t("calculatingDfr");
    subtitle.textContent = t("dfrCalculatingSubtitle");
    setStatus("loading", t("statusRunning"));
    return;
  }
  if (state.error) {
    title.textContent = t(state.error.titleKey);
    subtitle.textContent = state.error.message;
    setStatus("error", t("statusError"));
    return;
  }
  if (state.resultCurrent) {
    setStatus("done", t("statusReady"));
    return;
  }
  if (state.stale) {
    if (!state.result) setDfrIdleHeading();
    setStatus("warning", t("statusInputsChanged"));
    return;
  }
  setDfrIdleHeading();
}

function syncDfrForm() {
  const type = selectedDfrType();
  dfrNtruFields.classList.toggle("hidden", type !== "ntru");
  dfrLweFields.classList.toggle("hidden", type !== "lwe");
  renderDfrDistributionEditors(PREVIEW_MODE && renderedDfrType !== null && renderedDfrType !== type);
  const state = dfrState.snapshot();
  if (PREVIEW_MODE && (!state.result || state.result.type !== type)) {
    dfrState.setResult(previewDfrResult(type));
    updateRequestControls();
  }
  if (activeWorkspace === "dfr") renderDfrState();
}

function selectedDfrType() {
  return document.querySelector('input[name="dfrType"]:checked')?.value || "lwe";
}

function renderDfrDistributionEditors(useDefaults = false) {
  const previous = useDefaults ? {} : dfrDistributionState();
  const type = selectedDfrType();
  dfrDistributionEditors.replaceChildren(
    ...DFR_FIELDS[type].map((name) => createDfrDistributionEditor(name, previous[name], type)),
  );
  renderedDfrType = type;
}

function dfrDistributionState() {
  const state = {};
  dfrDistributionEditors.querySelectorAll(".dfr-distribution-editor").forEach((editor) => {
    const name = editor.dataset.dfrName;
    const distributionType = editor.querySelector("[data-dfr-distribution-type]")?.value;
    if (!name || !distributionType) return;
    const params = {};
    editor.querySelectorAll("[data-dfr-param]").forEach((field) => {
      params[field.dataset.dfrParam] = field.value;
    });
    state[name] = { type: distributionType, params };
  });
  return state;
}

function createDfrDistributionEditor(name, previous, dfrType) {
  const defaults = DFR_DEFAULTS[dfrType][name];
  const configuration = previous || defaults;
  const editor = document.createElement("section");
  editor.className = "dfr-distribution-editor";
  editor.dataset.dfrName = name;

  const header = document.createElement("div");
  header.className = "dfr-editor-header";
  const heading = document.createElement("strong");
  heading.textContent = name;
  const select = document.createElement("select");
  select.dataset.dfrDistributionType = "true";
  DFR_DISTRIBUTION_OPTIONS.forEach(([value, labelKey]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = t(labelKey);
    option.selected = value === configuration.type;
    select.appendChild(option);
  });
  header.append(heading, select);
  editor.appendChild(header);

  const parameters = document.createElement("div");
  parameters.className = "dfr-parameter-grid";
  const dimension = dfrDistributionDimension(dfrType, name);
  distributionParameterDefinitions(configuration.type, dimension).forEach((definition) => {
    parameters.appendChild(createDfrParameterField(name, definition, configuration.params || {}));
  });
  editor.appendChild(parameters);
  return editor;
}

function dfrDistributionDimension(type, name) {
  if (type === "ntru") return formFieldValue("dfrNtruN", 509);
  return ["e2", "ec2"].includes(name) ? formFieldValue("dfrLweN", 256) : formFieldValue("dfrLweM", 512);
}

function formFieldValue(name, fallback) {
  const value = Number(dfrForm.elements.namedItem(name)?.value);
  return Number.isInteger(value) && value > 0 ? value : fallback;
}

function distributionParameterDefinitions(type, dimension) {
  const number = (key, labelKey, value, min = "0") => ({ key, labelKey, value, inputType: "number", min });
  const text = (key, labelKey, value) => ({ key, labelKey, value, inputType: "text" });
  switch (type) {
    case "centered_binomial":
      return [number("eta", "eta", "2")];
    case "discrete_gaussian":
      return [number("stddev", "standardDeviation", "1", "0.0000001"), text("mean", "mean", "0")];
    case "uniform":
      return [text("lower_bound", "lowerBound", "-1"), text("upper_bound", "upperBound", "1")];
    case "uniform_mod":
      return [number("modulus", "modulusQ", "3329", "1")];
    case "t_uniform":
      return [number("b", "exponentB", "1")];
    case "sparse_ternary":
      return [
        number("plus_weight", "plusWeight", "1"),
        number("minus_weight", "minusWeight", "1"),
        number("dimension", "dimension", String(dimension), "1"),
      ];
    case "sparse_binary":
      return [number("weight", "weight", "1"), number("dimension", "dimension", String(dimension), "1")];
    case "lwr_floor_compression":
      return [number("q", "modulusQ", "3329", "2"), number("p", "compressionModulus", "1024", "2")];
    case "kyber_nearest_compression":
      return [number("q", "modulusQ", "3329", "2"), number("d", "compressionBits", "10", "1")];
    case "custom_pmf":
      return [{ key: "pmf", labelKey: "customPmf", value: '{"0":"1"}', inputType: "textarea", wide: true }];
    default:
      return [];
  }
}

function createDfrParameterField(distributionName, definition, values) {
  const label = document.createElement("label");
  if (definition.wide) label.className = "dfr-wide-field";
  const name = document.createElement("span");
  name.textContent = t(definition.labelKey);
  const control = definition.inputType === "textarea" ? document.createElement("textarea") : document.createElement("input");
  control.dataset.dfrParam = definition.key;
  control.name = `${distributionName}-${definition.key}`;
  control.value = values[definition.key] ?? definition.value;
  if (definition.inputType === "textarea") {
    control.rows = 3;
    control.placeholder = t("customPmfPlaceholder");
  } else {
    control.type = definition.inputType;
    if (definition.min !== undefined) control.min = definition.min;
    if (definition.inputType === "number") control.step = "any";
  }
  label.append(name, control);
  return label;
}

function buildDfrPayload() {
  const data = new FormData(dfrForm);
  const type = selectedDfrType();
  const payload = {
    type,
    precisionBits: Number(data.get("dfrPrecisionBits")),
    tailBits: Number(data.get("dfrTailBits")),
  };
  if (type === "ntru") {
    Object.assign(payload, {
      ringType: data.get("dfrRingType"),
      n: Number(data.get("dfrNtruN")),
      delta: String(data.get("dfrNtruDelta") || ""),
      p0: String(data.get("dfrP0") || ""),
      p1: String(data.get("dfrP1") || ""),
      p2: String(data.get("dfrP2") || ""),
      p3: String(data.get("dfrP3") || ""),
    });
  } else {
    Object.assign(payload, {
      m: Number(data.get("dfrLweM")),
      n: Number(data.get("dfrLweN")),
      delta: String(data.get("dfrLweDelta") || ""),
    });
  }

  dfrDistributionEditors.querySelectorAll(".dfr-distribution-editor").forEach((editor) => {
    const name = editor.dataset.dfrName;
    const typeSelect = editor.querySelector("[data-dfr-distribution-type]");
    const distribution = { type: typeSelect.value };
    editor.querySelectorAll("[data-dfr-param]").forEach((field) => {
      if (field.dataset.dfrParam === "pmf") {
        try {
          distribution.pmf = JSON.parse(field.value);
        } catch (_error) {
          throw new Error(t("invalidCustomPmf", { name }));
        }
      } else {
        distribution[field.dataset.dfrParam] = field.value;
      }
    });
    payload[name] = distribution;
  });
  return payload;
}

function fillSelect(select, options, fallback, formatter = null) {
  const previous = select.value;
  select.replaceChildren(
    ...options.map(([value, labelKey]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = formatter ? formatter(value, labelKey) : t(labelKey);
      return option;
    }),
  );
  select.value = options.some(([value]) => value === previous) ? previous : fallback;
  select.disabled = false;
}

function formatCompressionOption(value) {
  return t("compressionP", { value });
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

function applyLanguage() {
  document.documentElement.lang = currentLanguage === "zh" ? "zh-CN" : "en";
  languageSelect.value = currentLanguage;
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    node.textContent = t(node.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((node) => {
    node.setAttribute("placeholder", t(node.dataset.i18nPlaceholder));
  });
  document.querySelectorAll("[data-i18n-aria-label]").forEach((node) => {
    node.setAttribute("aria-label", t(node.dataset.i18nAriaLabel));
  });
}

function supportedLanguage(value) {
  const normalized = String(value || "en").toLowerCase();
  return normalized.startsWith("zh") ? "zh" : "en";
}

function t(key, values = {}) {
  const table = TRANSLATIONS[currentLanguage] || TRANSLATIONS.en;
  const template = table[key] || TRANSLATIONS.en[key] || key;
  return Object.entries(values).reduce(
    (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
    template,
  );
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

function apiUrl(path) {
  return path;
}

function apiHeaders(headers = {}) {
  return headers;
}

function previewFixtures() {
  return window.EASYLATTICE_PREVIEW_FIXTURES;
}

function previewRecommendation(payload) {
  const fixture = previewFixtures();
  if (!fixture?.recommendation) throw new Error("preview recommendation data is unavailable");
  return fixture.recommendation(payload);
}

function previewDfrResult(type) {
  const fixture = previewFixtures()?.dfr?.[type];
  if (!fixture) throw new Error(`preview ${type} DFR data is unavailable`);
  return JSON.parse(JSON.stringify(fixture));
}

function hasLiveApi() {
  return !PREVIEW_MODE && window.location.protocol !== "file:" && window.location.hostname !== "icarid-liu.github.io";
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function updateNttScaleLabel() {
  const value = Number(nttScale.value);
  const ternary = ringFamily.value === "ternary";
  if (value === 6) {
    nttScaleLabel.textContent = t("nttUnfriendly");
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
  nttScaleLabel.textContent = t("nttScale", { label });
}

void initializeApp();

async function initializeApp() {
  if (PREVIEW_MODE) {
    await loadPublicConfig();
    await requestRecommendation();
    return;
  }
  if (!hasLiveApi()) {
    setStatus("idle", t("statusIdle"));
    return;
  }
  await loadPublicConfig();
  await requestRecommendation();
}

async function loadPublicConfig() {
  try {
    const config = PREVIEW_MODE ? previewFixtures().config : await getJson("/api/config/public");
    publicConfig = config;
    renderPublicConfig(config);
    useLLM.disabled = !config.llm.configured;
    if (!config.llm.configured) {
      useLLM.checked = false;
    }
  } catch (_error) {
    return;
  }
}

function renderPublicConfig(config) {
  document.querySelector("#config-source").textContent = config.source;
  document.querySelector("#config-agent").textContent = t("configAgent");
  document.querySelector("#config-llm").textContent =
    config.llm.enabled
      ? t(config.llm.configured ? "configLlmReady" : "configLlmAuthMissing", {
          provider: config.llm.provider,
          model: config.llm.model,
        })
      : t("configLlmDisabled");
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
  document.querySelector("#config-estimator").textContent = t("configEstimator", {
    parts: estimatorParts.join(" · "),
  });
}
