(function expose(root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else if (root) {
    root.EasyLatticeModel = factory();
  }
})(typeof globalThis === "object" ? globalThis : this, function buildModel() {
  const LWE_RINGS = [
    { value: "power2", label: "x^n + 1" },
    { value: "ternary", label: "x^n - x^(n/2) + 1" },
  ];
  const NTRU_RINGS = [
    { value: "power2", label: "x^n + 1" },
    { value: "hps", label: "NTRU-HPS: x^N - 1" },
    { value: "hrss", label: "NTRU-HRSS: x^N - 1" },
    { value: "ntru_prime", label: "Streamlined NTRU Prime: x^n - x - 1" },
  ];
  const FORCED_RING_FAMILIES = new Set(["hps", "hrss", "ntru_prime"]);
  const STANDARD_ESTIMATOR_VARIANTS = new Set(["lwe", "lwr"]);
  const ENHANCED_ESTIMATOR_VARIANTS = new Set(["rlwe", "mlwe", "rlwr", "mlwr"]);

  function ringOptions(category) {
    return (category === "ntru" ? NTRU_RINGS : LWE_RINGS).map((item) => ({ ...item }));
  }

  function normalizeRingSelection(category, family, variant) {
    const matrixAllowed = category === "ntru" && !FORCED_RING_FAMILIES.has(family);
    return {
      family,
      variant: matrixAllowed ? variant : category === "ntru" ? "ring" : variant,
      matrixAllowed,
    };
  }

  function requiredEstimatorProfile(category, variant) {
    if (category === "ntru" && (variant === "matrix" || variant === "ring")) {
      return "standard";
    }
    if (category !== "lwe") return null;
    if (STANDARD_ESTIMATOR_VARIANTS.has(variant)) return "standard";
    if (ENHANCED_ESTIMATOR_VARIANTS.has(variant)) return "enhanced";
    return null;
  }

  function jobStagePresentation(stage) {
    const stages = {
      candidate_search: { key: "jobStageCandidateSearch", estimatorRunning: false },
      estimator_running: { key: "jobStageEstimatorRunning", estimatorRunning: true },
      finalizing: { key: "jobStageFinalizing", estimatorRunning: false },
    };
    return stages[stage] ? { ...stages[stage] } : null;
  }

  function resultPresentation(validationStatus, selectionStatus) {
    if (selectionStatus === "target_unmet") return { kind: "warning", key: "statusTargetUnmet" };
    if (validationStatus === "failed") return { kind: "error", key: "statusValidationFailed" };
    if (validationStatus === "partial") return { kind: "warning", key: "statusPartial" };
    if (validationStatus === "validated") return { kind: "done", key: "statusReady" };
    return { kind: "screened", key: "statusScreened" };
  }

  function compactRows(rows) {
    return rows.filter(([, value]) => value !== null && value !== undefined && value !== "");
  }

  function nextRevision(current) {
    return current + 1;
  }

  function acceptsResponse(startedRevision, currentRevision) {
    return startedRevision === currentRevision;
  }

  function createRequestState() {
    let revision = 0;
    let nextToken = 0;
    let activeRequest = null;
    let result = null;
    let resultRevision = null;
    let error = null;
    let errorRevision = null;

    function accepts(request) {
      return Boolean(
        request
        && activeRequest
        && request.token === activeRequest.token
        && request.revision === revision
        && activeRequest.revision === revision
      );
    }

    function snapshot() {
      const resultCurrent = result !== null && resultRevision === revision;
      const currentError = errorRevision === revision ? error : null;
      const inFlight = Boolean(activeRequest && activeRequest.revision === revision);
      return {
        revision,
        inFlight,
        metadata: inFlight ? { ...activeRequest.metadata } : null,
        result,
        resultCurrent,
        error: currentError,
        stale: !inFlight
          && currentError === null
          && !resultCurrent
          && (revision > 0 || resultRevision !== null || errorRevision !== null),
        copyEligible: resultCurrent,
      };
    }

    function invalidate() {
      revision = nextRevision(revision);
      activeRequest = null;
      return snapshot();
    }

    function begin(metadata = {}) {
      const request = Object.freeze({ token: ++nextToken, revision });
      activeRequest = { ...request, metadata: { ...metadata } };
      return request;
    }

    function update(request, metadata) {
      if (!accepts(request)) return false;
      activeRequest.metadata = { ...activeRequest.metadata, ...metadata };
      return true;
    }

    function acceptResult(request, nextResult) {
      if (!accepts(request)) return false;
      result = nextResult;
      resultRevision = revision;
      error = null;
      errorRevision = null;
      return true;
    }

    function acceptError(request, nextError) {
      if (!accepts(request)) return false;
      error = nextError;
      errorRevision = revision;
      return true;
    }

    function finish(request) {
      if (!activeRequest || !request || request.token !== activeRequest.token) return false;
      activeRequest = null;
      return true;
    }

    function setResult(nextResult) {
      result = nextResult;
      resultRevision = revision;
      error = null;
      errorRevision = null;
      return snapshot();
    }

    return Object.freeze({
      invalidate,
      begin,
      accepts,
      update,
      acceptResult,
      acceptError,
      finish,
      setResult,
      snapshot,
    });
  }

  return {
    ringOptions,
    normalizeRingSelection,
    requiredEstimatorProfile,
    jobStagePresentation,
    resultPresentation,
    compactRows,
    nextRevision,
    acceptsResponse,
    createRequestState,
  };
});
