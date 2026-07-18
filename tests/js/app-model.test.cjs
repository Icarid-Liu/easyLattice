const test = require("node:test");
const assert = require("node:assert/strict");
const model = require("../../static/app-model.js");

test("exports the exact browser model API", () => {
  assert.deepEqual(Object.keys(model).sort(), [
    "acceptsResponse",
    "compactRows",
    "nextRevision",
    "normalizeRingSelection",
    "resultPresentation",
    "ringOptions",
  ]);
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
