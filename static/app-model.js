(function expose(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.EasyLatticeModel = api;
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

  return {
    ringOptions,
    normalizeRingSelection,
    resultPresentation,
    compactRows,
    nextRevision,
    acceptsResponse,
  };
});
