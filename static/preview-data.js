(() => {
  const warning = "This is a static preview fixture. Run the local service for live security and DFR calculations.";
  const unionBoundWarning = "Vector DFR uses a union bound and does not assume independent output coefficients.";
  const eccNote = "Apply a scheme-specific error-correction calculation outside this module.";

  const clone = (value) => JSON.parse(JSON.stringify(value));
  const distribution = (name, stddev, support = [-1, 1]) => ({ name, stddev, support });
  const ringCoefficientProfiles = (n, ringType) => {
    const positive = Array(n).fill(0);
    const negative = Array(n).fill(0);
    for (let degree = 0; degree < 2 * n - 1; degree += 1) {
      const multiplicity = degree < n ? degree + 1 : 2 * n - 1 - degree;
      if (degree < n) {
        positive[degree] += multiplicity;
        continue;
      }
      const output = degree - n;
      if (ringType === "negacyclic") {
        negative[output] += multiplicity;
      } else {
        positive[output] += multiplicity;
        if (ringType === "ntru_prime") positive[output + 1] += multiplicity;
      }
    }
    return positive.map((positiveTerms, index) => ({
      positive_terms: positiveTerms,
      negative_terms: negative[index],
    }));
  };

  const lweCandidate = {
    ring: {
      family_id: "power2",
      family: "2-power cyclotomic",
      n: 512,
      cyclotomic_index: 1024,
      polynomial: "x^512 + 1",
      quotient: "Z_257[x] / (x^512 + 1)",
    },
    modulus: {
      q: 257,
      bits: 9,
      q_minus_1_factorization: "2^8",
      ntt_condition: "256 | q - 1; 512 does not divide q - 1",
      ntt_quality: "2_layers_remaining",
      ntt_layers_remaining: 2,
      polynomial_factorization: "x^512 + 1 splits into 128 quartic factors over F_q",
    },
    distribution: {
      name: "Xs=ST(l0=1, l1=0), Xe=ST(l0=3, l1=2)",
      secret: distribution("ST(l0=1, l1=0)", "0.707106781", [-1, 1]),
      error: distribution("ST(l0=3, l1=2)", "0.233853587", [-1, 1]),
    },
    security: {
      source: "fast-screen",
      source_code: "fast_screen",
      classical_bits: 129.6,
      quantum_bits: 117.6,
      matzov_bits: 129.6,
      matzov_quantum_bits: 117.6,
      adps16_core_svp_bits: 129.6,
      adps16_quantum_bits: 117.6,
    },
    warnings: [warning],
    warning_codes: ["screen_scheme_not_bound"],
    visual_scores: {
      security: { score: 0.253 },
      compactness: { score: 0.682 },
      performance: { score: 0.5 },
    },
  };

  const ntruCandidate = {
    ring: {
      family_id: "hps",
      family: "NTRU-HPS",
      n: 592,
      cyclotomic_index: null,
      polynomial: "x^593 - 1 with one relation removed by the estimator",
      quotient: "NTRU-HPS style mod q=2048, public polynomial degree N=593",
      ntru_type: "circulant",
      preset: null,
    },
    modulus: {
      q: 2048,
      bits: 11,
      q_minus_1_factorization: "23 * 89",
      ntt_condition: null,
      ntt_friendly: null,
      ntt_quality: null,
      ntt_layers_remaining: null,
      polynomial_factorization: null,
    },
    distribution: {
      name: "HPS-style ternary",
      fixed_weight: null,
      secret: distribution("ternary(-1, 0, 1)", "0.816496581", [-1, 1]),
      error: distribution("ternary(-1, 0, 1)", "0.816496581", [-1, 1]),
    },
    security: {
      source: "ntru-reference-screen",
      source_code: "ntru_reference_screen",
      classical_bits: 134.2,
      quantum_bits: 121.4,
      matzov_bits: 134.2,
      matzov_quantum_bits: 121.4,
      adps16_core_svp_bits: 132.8,
      adps16_quantum_bits: 120.1,
      reference_classical_bits: 134.2,
      reference_quantum_bits: 121.4,
      nist_category: null,
    },
    warnings: [warning],
    warning_codes: ["screen_scheme_not_bound"],
    visual_scores: {
      security: { score: 0.262 },
      compactness: { score: 0.591 },
      performance: { score: 0.6 },
    },
  };

  const ntruPrimeCandidate = clone(ntruCandidate);
  Object.assign(ntruPrimeCandidate.ring, {
    family_id: "ntru_prime",
    family: "Streamlined NTRU Prime",
    n: 653,
    polynomial: "x^653 - x - 1",
    quotient: "Z_4621[x] / (x^653 - x - 1)",
    preset: "sntrup653",
  });
  Object.assign(ntruPrimeCandidate.modulus, {
    q: 4621,
    bits: 13,
    q_minus_1_factorization: "2^2 * 3 * 5 * 7 * 11",
  });
  Object.assign(ntruPrimeCandidate.distribution, {
    name: "SparseTernary(p=144, m=144) / UniformMod(3)",
    fixed_weight: 288,
    secret: distribution("SparseTernary(p=144, m=144)", "0.664112", [-1, 1]),
    error: distribution("UniformMod(3)", "0.816496581", [-1, 1]),
  });
  Object.assign(ntruPrimeCandidate.security, {
    classical_bits: 129,
    quantum_bits: 117,
    matzov_bits: 129,
    matzov_quantum_bits: 117,
    adps16_core_svp_bits: 129,
    adps16_quantum_bits: 117,
    reference_classical_bits: 129,
    reference_quantum_bits: 117,
    nist_category: 1,
  });

  function withSelection(candidate, payload) {
    const selected = payload.securityModel === "quantum"
      ? (payload.redCostModel === "adps16" ? candidate.security.adps16_quantum_bits : candidate.security.matzov_quantum_bits)
      : (payload.redCostModel === "adps16" ? candidate.security.adps16_core_svp_bits : candidate.security.matzov_bits);
    const target = Number(payload.targetSecurity) || 128;
    candidate.selection = {
      target_security: target,
      security_model: payload.securityModel || "classical",
      selected_security_bits: selected,
      margin_bits: Math.round((selected - target) * 10) / 10,
      meets_target: selected >= target,
      status: selected >= target ? "target_met" : "target_unmet",
      security_level: "NIST-I",
    };
    return candidate;
  }

  function makeAlternative(base, q, bits, selected, payload) {
    const candidate = clone(base);
    candidate.modulus.q = q;
    candidate.modulus.bits = bits;
    candidate.selection = {
      target_security: Number(payload.targetSecurity) || 128,
      security_model: payload.securityModel || "classical",
      selected_security_bits: selected,
      margin_bits: Math.round((selected - (Number(payload.targetSecurity) || 128)) * 10) / 10,
      meets_target: selected >= (Number(payload.targetSecurity) || 128),
      status: selected >= (Number(payload.targetSecurity) || 128) ? "target_met" : "target_unmet",
      security_level: "NIST-I",
    };
    return candidate;
  }

  function recommendation(payload) {
    const isNtru = payload.hardProblemCategory === "ntru";
    const candidateFixture = isNtru && payload.ringFamily === "ntru_prime"
      ? ntruPrimeCandidate
      : isNtru ? ntruCandidate : lweCandidate;
    const candidate = withSelection(clone(candidateFixture), payload);
    const request = {
      target_security: Number(payload.targetSecurity) || 128,
      hard_problem_category: isNtru ? "ntru" : payload.hardProblemCategory || "lwe",
      hard_problem_variant: isNtru ? payload.hardProblemVariant || "ring" : payload.hardProblemVariant || "rlwe",
      ring_family: payload.ringFamily || "power2",
      security_model: payload.securityModel || "classical",
      red_cost_model: payload.redCostModel || "matzov",
    };
    const alternatives = isNtru
      ? [makeAlternative(candidateFixture, candidate.modulus.q, candidate.modulus.bits, 139.1, payload), makeAlternative(candidateFixture, candidate.modulus.q, candidate.modulus.bits, 132.8, payload)]
      : [makeAlternative(lweCandidate, 769, 10, 129.9, payload), makeAlternative(lweCandidate, 7681, 13, 144.6, payload)];
    const profile = isNtru || ["lwe", "lwr"].includes(request.hard_problem_variant)
      ? "standard"
      : "enhanced";
    const validation = payload.useEstimator
      ? {
          requested: true,
          status: "failed",
          profile,
          estimator_commit: null,
          attempted_candidates: 1,
          successful_candidates: 0,
          covered_candidates: 0,
          eligible_candidates: alternatives.length + 1,
          message_codes: ["validation_config_missing"],
        }
      : {
          requested: false,
          status: "not_requested",
          profile,
          estimator_commit: null,
          attempted_candidates: 0,
          successful_candidates: 0,
          covered_candidates: 0,
          eligible_candidates: alternatives.length + 1,
          message_codes: [],
        };
    candidate.warning_codes = [...new Set([...candidate.warning_codes, ...validation.message_codes])];
    return {
      agent: { name: "static-preview", llm_used: false, notes: ["Preview data is illustrative only."] },
      request,
      recommendation: candidate,
      alternatives,
      validation,
      search: { elapsed_ms: 0, generated_candidates: isNtru ? 3 : 7168 },
      next_question: "Run the local service to evaluate parameters and bind them to a concrete scheme.",
      next_step_code: "bind_scheme_constraints",
    };
  }

  const dfr = {
    ntru: {
      ok: true,
      type: "ntru",
      formula: "p0*(g*s)_n + p1*(f*e)_n + p2*(f*m)_n + p3*e",
      success_condition: "|E| <= Delta",
      dimensions: { n: 509 },
      delta: "1024",
      precision_bits: 512,
      precision_decimal_digits: 167,
      tail_bits: 128,
      single_coefficient_dfr_log2: "-552.2346327506126",
      vector_dfr_log2_before_ecc: "-543.2431109045369",
      single_coefficient_failure_probability: "5.76515379864975E-167",
      vector_failure_probability_before_ecc: "2.93446328351272E-164",
      single_coefficient_semantics: "worst_coefficient",
      vector_aggregation: "union_bound",
      tail_probability_upper_bound: "0",
      error_support: { size: 3435, minimum: "-2036", maximum: "2036" },
      distributions: {
        g: { support_size: 3, support: ["-1", "1"] },
        f: { support_size: 3, support: ["-1", "1"] },
        s: { support_size: 3, support: ["-1", "1"] },
        e: { support_size: 3, support: ["-1", "1"] },
        m: { support_size: 3, support: ["-1", "1"] },
      },
      coefficients: { p0: "3", p1: "0", p2: "1", p3: "0" },
      ring_type: "cyclic",
      ring_polynomial: "x^509 - 1",
      coefficient_dfr: {
        worst_index: 0,
        distinct_profiles: 1,
        profiles: ringCoefficientProfiles(509, "cyclic"),
      },
      warnings: [unionBoundWarning],
      warning_codes: ["dfr_union_bound"],
      error_correction: { included: false, code: "dfr_ecc_external", note: eccNote },
    },
    lwe: {
      ok: true,
      type: "lwe",
      formula: "((e1 + ec1)*s)_m + (e*r)_m + e2 + ec2",
      success_condition: "|E| <= Delta",
      dimensions: { m: 512, n: 256 },
      delta: "832",
      precision_bits: 512,
      precision_decimal_digits: 167,
      tail_bits: 128,
      single_coefficient_dfr_log2: "-147.135837014246",
      vector_dfr_log2_before_ecc: "-139.135837014246",
      single_coefficient_failure_probability: "5.10152032873539E-45",
      vector_failure_probability_before_ecc: "1.30598920415626E-42",
      single_coefficient_semantics: "identical_coefficient_model",
      vector_aggregation: "union_bound",
      tail_probability_upper_bound: "0",
      error_support: { size: 17073, minimum: "-10858", maximum: "10826" },
      distributions: {
        s: { support_size: 7, support: ["-3", "3"] },
        e: { support_size: 7, support: ["-3", "3"] },
        e1: { support_size: 5, support: ["-2", "2"] },
        r: { support_size: 7, support: ["-3", "3"] },
        e2: { support_size: 5, support: ["-2", "2"] },
        ec1: { support_size: 5, support: ["-2", "2"] },
        ec2: { support_size: 209, support: ["-104", "104"] },
      },
      warnings: [unionBoundWarning],
      warning_codes: ["dfr_union_bound"],
      error_correction: { included: false, code: "dfr_ecc_external", note: eccNote },
    },
  };

  const ntruNegacyclicDfr = clone(dfr.ntru);
  ntruNegacyclicDfr.ring_type = "negacyclic";
  ntruNegacyclicDfr.ring_polynomial = "x^509 + 1";
  ntruNegacyclicDfr.coefficient_dfr = {
    worst_index: 0,
    distinct_profiles: 509,
    profiles: ringCoefficientProfiles(509, "negacyclic"),
  };

  const ntruPrimeDfr = clone(dfr.ntru);
  ntruPrimeDfr.ring_type = "ntru_prime";
  ntruPrimeDfr.ring_polynomial = "x^509 - x - 1";
  ntruPrimeDfr.coefficient_dfr = {
    worst_index: 1,
    distinct_profiles: 509,
    profiles: ringCoefficientProfiles(509, "ntru_prime"),
  };
  ntruPrimeDfr.warning_codes.push("ntru_prime_coefficient_marginal");
  ntruPrimeDfr.warnings.push(
    "NTRU Prime ring products use a coefficient-marginal approximation; the vector union bound makes no joint independence claim.",
  );

  dfr.ntru_rings = {
    cyclic: dfr.ntru,
    negacyclic: ntruNegacyclicDfr,
    ntru_prime: ntruPrimeDfr,
  };

  window.EASYLATTICE_PREVIEW_FIXTURES = {
    config: {
      source: "static-preview",
      llm: { enabled: false, configured: false },
      estimator: {
        remote_configured: false,
        lattice_estimator_path: null,
        sage_binary: "not available",
        version: null,
      },
    },
    recommendation,
    dfr,
  };
})();
