const test = require("node:test");
const assert = require("node:assert/strict");
delete globalThis.EasyLatticeModel;
const model = require("../../static/app-model.js");

test("exports the exact browser model API", () => {
  assert.deepEqual(Object.keys(model).sort(), [
    "acceptsResponse",
    "compactRows",
    "createRequestState",
    "jobStagePresentation",
    "nextRevision",
    "normalizeRingSelection",
    "requiredEstimatorProfile",
    "resultPresentation",
    "ringOptions",
  ]);
  assert.equal(globalThis.EasyLatticeModel, undefined);
});

test("estimator profiles follow the selected hard problem", () => {
  assert.equal(model.requiredEstimatorProfile("ntru", "ring"), "standard");
  assert.equal(model.requiredEstimatorProfile("ntru", "matrix"), "standard");
  assert.equal(model.requiredEstimatorProfile("lwe", "lwe"), "standard");
  assert.equal(model.requiredEstimatorProfile("lwe", "lwr"), "standard");
  for (const variant of ["rlwe", "mlwe", "rlwr", "mlwr"]) {
    assert.equal(model.requiredEstimatorProfile("lwe", variant), "enhanced");
  }
  assert.equal(model.requiredEstimatorProfile("lwe", "sis"), null);
  assert.equal(model.requiredEstimatorProfile("unknown", "rlwe"), null);
});

test("job stages map to stable translation keys", () => {
  assert.deepEqual(model.jobStagePresentation("candidate_search"), {
    key: "jobStageCandidateSearch",
    estimatorRunning: false,
  });
  assert.deepEqual(model.jobStagePresentation("estimator_running"), {
    key: "jobStageEstimatorRunning",
    estimatorRunning: true,
  });
  assert.deepEqual(model.jobStagePresentation("finalizing"), {
    key: "jobStageFinalizing",
    estimatorRunning: false,
  });
  assert.equal(model.jobStagePresentation("unknown"), null);
});

test("ring options follow the hard problem", () => {
  assert.deepEqual(model.ringOptions("lwe"), [
    { value: "power2", label: "x^n + 1" },
    { value: "ternary", label: "x^n - x^(n/2) + 1" },
  ]);
  assert.deepEqual(model.ringOptions("ntru"), [
    { value: "power2", label: "x^n + 1" },
    { value: "hps", label: "NTRU-HPS: x^N - 1" },
    { value: "hrss", label: "NTRU-HRSS: x^N - 1" },
    { value: "ntru_prime", label: "Streamlined NTRU Prime: x^n - x - 1" },
  ]);
});

test("ring options are cloned and unknown categories use LWE defaults", () => {
  const first = model.ringOptions("ntru");
  const second = model.ringOptions("ntru");
  assert.notStrictEqual(first, second);
  assert.notStrictEqual(first[0], second[0]);

  first[0].value = "changed";
  first.push({ value: "extra", label: "extra" });
  assert.deepEqual(model.ringOptions("ntru").map((item) => item.value), [
    "power2",
    "hps",
    "hrss",
    "ntru_prime",
  ]);
  assert.deepEqual(model.ringOptions("unknown").map((item) => item.value), ["power2", "ternary"]);
});

test("classic and prime NTRU families force the ring variant", () => {
  for (const family of ["hps", "hrss", "ntru_prime"]) {
    assert.deepEqual(model.normalizeRingSelection("ntru", family, "matrix"), {
      family,
      variant: "ring",
      matrixAllowed: false,
    });
  }
  assert.deepEqual(model.normalizeRingSelection("ntru", "power2", "matrix"), {
    family: "power2",
    variant: "matrix",
    matrixAllowed: true,
  });
});

test("LWE normalization preserves its selected variant without allowing NTRU matrix", () => {
  assert.deepEqual(model.normalizeRingSelection("lwe", "ternary", "mlwe"), {
    family: "ternary",
    variant: "mlwe",
    matrixAllowed: false,
  });
});

test("status presentation applies the exact safety priority", () => {
  assert.deepEqual(model.resultPresentation("failed", "target_unmet"), {
    kind: "warning",
    key: "statusTargetUnmet",
  });
  assert.deepEqual(model.resultPresentation("failed", "target_met"), {
    kind: "error",
    key: "statusValidationFailed",
  });
  assert.deepEqual(model.resultPresentation("partial", "target_met"), {
    kind: "warning",
    key: "statusPartial",
  });
  assert.deepEqual(model.resultPresentation("validated", "target_met"), {
    kind: "done",
    key: "statusReady",
  });
  assert.deepEqual(model.resultPresentation("not_requested", "target_met"), {
    kind: "screened",
    key: "statusScreened",
  });
});

test("compact rows removes absent values while retaining falsey data", () => {
  const rows = [
    ["zero", 0],
    ["false", false],
    ["null", null],
    ["undefined", undefined],
    ["empty", ""],
    ["space", " "],
  ];
  const snapshot = rows.slice();

  assert.deepEqual(model.compactRows(rows), [
    ["zero", 0],
    ["false", false],
    ["space", " "],
  ]);
  assert.deepEqual(rows, snapshot);
});

test("revision helpers increment and accept only strict current matches", () => {
  assert.equal(model.nextRevision(0), 1);
  assert.equal(model.nextRevision(8), 9);
  assert.equal(model.acceptsResponse(3, 3), true);
  assert.equal(model.acceptsResponse(2, 3), false);
  assert.equal(model.acceptsResponse("3", 3), false);
});

test("invalidating a request unlocks state for a newer request", () => {
  const state = model.createRequestState();
  const first = state.begin({ subtitleKey: "generatingSubtitle" });
  assert.equal(state.snapshot().inFlight, true);

  state.invalidate();
  assert.equal(state.accepts(first), false);
  assert.deepEqual(state.snapshot(), {
    revision: 1,
    inFlight: false,
    metadata: null,
    result: null,
    resultCurrent: false,
    error: null,
    stale: true,
    copyEligible: false,
  });

  const second = state.begin({ subtitleKey: "estimatorSubtitle" });
  assert.equal(state.accepts(second), true);
  assert.notEqual(second.token, first.token);
});

test("stale finish cannot alter a newer active request", () => {
  const state = model.createRequestState();
  const first = state.begin();
  state.invalidate();
  const second = state.begin({ subtitleKey: "newRequest" });

  assert.equal(state.acceptResult(first, { id: "old" }), false);
  assert.equal(state.acceptError(first, { message: "old failure" }), false);
  assert.equal(state.finish(first), false);
  assert.equal(state.snapshot().inFlight, true);
  assert.deepEqual(state.snapshot().metadata, { subtitleKey: "newRequest" });

  assert.equal(state.acceptResult(second, { id: "new" }), true);
  assert.equal(state.finish(second), true);
  assert.equal(state.snapshot().inFlight, false);
  assert.deepEqual(state.snapshot().result, { id: "new" });
});

test("current request errors survive until rendered or invalidated", () => {
  const state = model.createRequestState();
  const request = state.begin();
  const error = { titleKey: "requestFailed", message: "offline" };

  assert.equal(state.acceptError(request, error), true);
  assert.equal(state.finish(request), true);
  assert.equal(state.snapshot().error, error);
  assert.equal(state.snapshot().stale, false);

  state.invalidate();
  assert.equal(state.snapshot().error, null);
  assert.equal(state.snapshot().stale, true);
});

test("running metadata updates only for the active request", () => {
  const state = model.createRequestState();
  const first = state.begin({ subtitleKey: "estimatorSubtitle" });
  assert.equal(state.update(first, {
    subtitleKey: "estimatorWaiting",
    subtitleValues: { status: "running" },
  }), true);
  assert.deepEqual(state.snapshot().metadata, {
    subtitleKey: "estimatorWaiting",
    subtitleValues: { status: "running" },
  });

  state.invalidate();
  const second = state.begin({ subtitleKey: "generatingSubtitle" });
  assert.equal(state.update(first, { subtitleKey: "old" }), false);
  assert.deepEqual(state.snapshot().metadata, { subtitleKey: "generatingSubtitle" });
  assert.equal(state.finish(second), true);
});

test("current and preview results control copy eligibility by revision", () => {
  const state = model.createRequestState();
  const initial = { id: "initial" };
  state.setResult(initial);
  assert.equal(state.snapshot().resultCurrent, true);
  assert.equal(state.snapshot().copyEligible, true);

  state.invalidate();
  assert.equal(state.snapshot().resultCurrent, false);
  assert.equal(state.snapshot().copyEligible, false);

  const previewFixture = { id: "preview-switch" };
  state.setResult(previewFixture);
  assert.equal(state.snapshot().result, previewFixture);
  assert.equal(state.snapshot().resultCurrent, true);
  assert.equal(state.snapshot().copyEligible, true);
});
