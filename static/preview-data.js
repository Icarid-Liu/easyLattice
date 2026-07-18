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
    warning_codes: ["screen_scheme_not_bound", "preview_fixture_notice"],
    visual_scores: {
      security: { score: 0.253 },
      compactness: { score: 0.682 },
      performance: { score: 0.5 },
    },
  };

  const lweAlternativeCandidates = [
    {
      ring: {
        family_id: "power2",
        family: "2-power cyclotomic",
        n: 512,
        cyclotomic_index: 1024,
        polynomial: "x^512 + 1",
        quotient: "Z_769[x] / (x^512 + 1)",
      },
      modulus: {
        q: 769,
        bits: 10,
        q_minus_1_factorization: "2^8 * 3",
        ntt_condition: "256 | q - 1; 512 does not divide q - 1",
        ntt_quality: "2_layers_remaining",
        ntt_layers_remaining: 2,
        polynomial_factorization: "x^512 + 1 splits into 128 quartic factors over F_q",
      },
      distribution: {
        name: "Xs=ST(l0=1, l1=0), Xe=CBD(1)",
        secret: distribution("ST(l0=1, l1=0)", "0.707106781", [-1, 1]),
        error: distribution("CBD(1)", "0.707106781", [-1, 1]),
      },
      security: {
        source: "fast-screen",
        source_code: "fast_screen",
        classical_bits: 129.9,
        quantum_bits: 117.9,
        matzov_bits: 129.9,
        matzov_quantum_bits: 117.9,
        adps16_core_svp_bits: 129.9,
        adps16_quantum_bits: 117.9,
      },
      warnings: [warning],
      warning_codes: ["screen_scheme_not_bound", "preview_fixture_notice"],
      visual_scores: {
        security: { score: 0.254 },
        compactness: { score: 0.625 },
        performance: { score: 0.5 },
      },
    },
  ];

  const ntruSecurity = (classical, quantum = null, nistCategory = null) => ({
    source: "ntru-reference-screen",
    source_code: "ntru_reference_screen",
    classical_bits: classical,
    quantum_bits: quantum,
    matzov_bits: classical,
    matzov_quantum_bits: quantum,
    adps16_core_svp_bits: classical,
    adps16_quantum_bits: quantum,
    reference_classical_bits: classical,
    reference_quantum_bits: quantum,
    nist_category: nistCategory,
  });
  const ntruFixture = (ring, modulus, distributionProfile, security) => ({
    ring,
    modulus,
    distribution: distributionProfile,
    security,
    warnings: [warning],
    warning_codes: ["screen_scheme_not_bound", "preview_fixture_notice"],
    visual_scores: {
      security: { score: 0.262 },
      compactness: { score: 0.591 },
      performance: { score: 0.6 },
    },
  });
  const inapplicableNtt = {
    ntt_condition: null,
    ntt_friendly: null,
    ntt_quality: null,
    ntt_layers_remaining: null,
    polynomial_factorization: null,
  };
  const power2NtruCandidate = (q, factorization, profileName, stddev, support, bits) => ntruFixture(
    {
      family_id: "power2",
      family: "2-power cyclotomic NTRU",
      n: 512,
      cyclotomic_index: 1024,
      polynomial: "x^512 + 1",
      quotient: `Z_${q}[x] / (x^512 + 1)`,
      ntru_type: "circulant",
      preset: null,
    },
    {
      q,
      bits: Math.floor(Math.log2(q)) + 1,
      q_minus_1_factorization: factorization,
      ntt_condition: "512 | q - 1; one layer below full split",
      ntt_friendly: true,
      ntt_quality: "selected_scale",
      ntt_layers_remaining: null,
      polynomial_factorization: null,
    },
    {
      name: profileName,
      fixed_weight: null,
      secret: distribution(profileName, stddev, support),
      error: distribution(profileName, stddev, support),
    },
    ntruSecurity(bits),
  );
  const hpsCandidate = (publicN, bits, errorStddev) => {
    const n = publicN - 1;
    return ntruFixture(
      {
        family_id: "hps",
        family: "NTRU-HPS style",
        n,
        cyclotomic_index: null,
        polynomial: `x^${publicN} - 1 with one relation removed by the estimator`,
        quotient: `NTRU-HPS style mod q=2048, public polynomial degree N=${publicN}`,
        ntru_type: "circulant",
        preset: null,
      },
      { q: 2048, bits: 11, q_minus_1_factorization: "23 * 89", ...inapplicableNtt },
      {
        name: "Xs=UniformMod(3), Xe=SparseTernary(p=127, m=127)",
        fixed_weight: null,
        secret: distribution("UniformMod(3)", "0.816496581", [-1, 1]),
        error: distribution("SparseTernary(p=127, m=127)", errorStddev, [-1, 1]),
      },
      ntruSecurity(bits),
    );
  };
  const hrssCandidate = (publicN, bits) => {
    const n = publicN - 1;
    return ntruFixture(
      {
        family_id: "hrss",
        family: "NTRU-HRSS style",
        n,
        cyclotomic_index: null,
        polynomial: `x^${publicN} - 1 with one relation removed by the estimator`,
        quotient: `NTRU-HRSS style mod q=8192, public polynomial degree N=${publicN}`,
        ntru_type: "circulant",
        preset: null,
      },
      { q: 8192, bits: 13, q_minus_1_factorization: "8191", ...inapplicableNtt },
      {
        name: "UniformMod(3)",
        fixed_weight: null,
        secret: distribution("UniformMod(3)", "0.816496581", [-1, 1]),
        error: distribution("UniformMod(3)", "0.816496581", [-1, 1]),
      },
      ntruSecurity(bits),
    );
  };
  const ntruPrimeCandidate = (
    preset,
    n,
    q,
    factorization,
    signWeight,
    fixedWeight,
    stddev,
    classical,
    quantum,
    category,
  ) => ntruFixture(
    {
      family_id: "ntru_prime",
      family: "Streamlined NTRU Prime",
      n,
      cyclotomic_index: null,
      polynomial: `x^${n} - x - 1`,
      quotient: `Z_${q}[x] / (x^${n} - x - 1)`,
      ntru_type: "circulant",
      preset,
    },
    {
      q,
      bits: Math.floor(Math.log2(q)) + 1,
      q_minus_1_factorization: factorization,
      ...inapplicableNtt,
    },
    {
      name: `Xs=SparseTernary(p=${signWeight}, m=${signWeight}), Xe=UniformMod(3)`,
      fixed_weight: fixedWeight,
      secret: distribution(`SparseTernary(p=${signWeight}, m=${signWeight})`, stddev, [-1, 1]),
      error: distribution("UniformMod(3)", "0.816496581", [-1, 1]),
    },
    ntruSecurity(classical, quantum, category),
  );
  const ntruPreviewFamilies = {
    power2: {
      generatedCandidates: 5,
      eligibleCandidates: 4,
      candidate: power2NtruCandidate(
        7681,
        "2^9 * 3 * 5",
        "ST(l0=2, l1=0) + CBD(5) + CBD(8)",
        "2.62202212",
        [-14, 14],
        131.4,
      ),
      alternatives: [
        power2NtruCandidate(10753, "2^9 * 3 * 7", "CBD(2) + CBD(8) + CBD(8)", "3", [-18, 18], 130.2),
        power2NtruCandidate(11777, "2^9 * 23", "CBD(2) + CBD(8) + CBD(8)", "3", [-18, 18], 128.4),
      ],
    },
    hps: {
      generatedCandidates: 4,
      eligibleCandidates: 4,
      candidate: hpsCandidate(593, 128.6, "0.655022178"),
      alternatives: [
        hpsCandidate(599, 129.9, "0.65172783"),
        hpsCandidate(607, 131.8, "0.647411704"),
      ],
    },
    hrss: {
      generatedCandidates: 4,
      eligibleCandidates: 4,
      candidate: hrssCandidate(673, 130.8),
      alternatives: [hrssCandidate(677, 131.6), hrssCandidate(683, 133.1)],
    },
    ntru_prime: {
      generatedCandidates: 6,
      eligibleCandidates: 6,
      candidate: ntruPrimeCandidate(
        "sntrup653", 653, 4621, "2^2 * 3 * 5 * 7 * 11", 144, 288, "0.664109439", 129, 117, 1,
      ),
      alternatives: [
        ntruPrimeCandidate(
          "sntrup761", 761, 4591, "2 * 3^3 * 5 * 17", 143, 286, "0.613042648", 153, 139, 2,
        ),
        ntruPrimeCandidate(
          "sntrup857", 857, 5167, "2 * 3^2 * 7 * 41", 161, 322, "0.612967608", 175, 159, 3,
        ),
      ],
    },
  };
  const LWE_CANDIDATE_POOL = {
    generatedCandidates: 7168,
    eligibleCandidates: 7168,
  };

  function securityLevelForBits(bits) {
    if (bits === null || bits === undefined) return "unclassified";
    if (bits < 128) return "below NIST-I";
    if (bits < 192) return "NIST-I";
    if (bits < 256) return "NIST-III";
    return "NIST-V";
  }

  function withSelection(candidate, payload) {
    const selected = payload.securityModel === "quantum"
      ? (payload.redCostModel === "adps16" ? candidate.security.adps16_quantum_bits : candidate.security.matzov_quantum_bits)
      : (payload.redCostModel === "adps16" ? candidate.security.adps16_core_svp_bits : candidate.security.matzov_bits);
    const target = Number(payload.targetSecurity) || 128;
    const hasEstimate = selected !== null && selected !== undefined;
    const meetsTarget = hasEstimate && selected >= target;
    candidate.selection = {
      target_security: target,
      security_model: payload.securityModel || "classical",
      selected_security_bits: selected,
      margin_bits: hasEstimate ? Math.round((selected - target) * 10) / 10 : null,
      meets_target: meetsTarget,
      status: meetsTarget ? "target_met" : "target_unmet",
      security_level: securityLevelForBits(selected),
    };
    return candidate;
  }

  function recommendation(payload) {
    const isNtru = payload.hardProblemCategory === "ntru";
    const family = isNtru && ntruPreviewFamilies[payload.ringFamily]
      ? payload.ringFamily
      : "power2";
    const familyFixture = isNtru ? ntruPreviewFamilies[family] : null;
    const requestedVariant = payload.hardProblemVariant || (isNtru ? "ring" : "rlwe");
    const effectiveVariant = isNtru
      ? (family === "power2" && requestedVariant === "matrix" ? "matrix" : "ring")
      : requestedVariant;
    const candidateFixture = isNtru ? familyFixture.candidate : lweCandidate;
    const candidate = withSelection(clone(candidateFixture), payload);
    if (isNtru && family === "power2") {
      candidate.ring.ntru_type = effectiveVariant === "matrix" ? "matrix" : "circulant";
    }
    const candidatePool = isNtru ? familyFixture : LWE_CANDIDATE_POOL;
    const request = {
      target_security: Number(payload.targetSecurity) || 128,
      hard_problem_category: isNtru ? "ntru" : payload.hardProblemCategory || "lwe",
      hard_problem_variant: effectiveVariant,
      ring_family: isNtru ? family : payload.ringFamily || "power2",
      security_model: payload.securityModel || "classical",
      red_cost_model: payload.redCostModel || "matzov",
    };
    const alternativeFixtures = isNtru ? familyFixture.alternatives : lweAlternativeCandidates;
    const alternatives = alternativeFixtures.map((fixture) => {
      const alternative = withSelection(clone(fixture), payload);
      if (isNtru && family === "power2") {
        alternative.ring.ntru_type = effectiveVariant === "matrix" ? "matrix" : "circulant";
      }
      return alternative;
    });
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
          eligible_candidates: candidatePool.eligibleCandidates,
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
          eligible_candidates: candidatePool.eligibleCandidates,
          message_codes: [],
        };
    candidate.warning_codes = [...new Set([...candidate.warning_codes, ...validation.message_codes])];
    return {
      agent: { name: "static-preview", llm_used: false, notes: ["Preview data is illustrative only."] },
      request,
      recommendation: candidate,
      alternatives,
      validation,
      search: { elapsed_ms: 0, generated_candidates: candidatePool.generatedCandidates },
      next_question: "Run the local service to evaluate parameters and bind them to a concrete scheme.",
      next_step_code: "bind_scheme_constraints",
    };
  }

  const CYCLIC_NTRU_DFR_DIMENSION = 509;
  const CYCLIC_NTRU_SINGLE_FAILURE = "5.7651537986497525006899801225417864725039E-167";
  const coefficientDfr = (
    dimension,
    ringType,
    worstIndex,
    distinctProfiles,
    failureProbabilities,
  ) => ({
    worst_index: worstIndex,
    distinct_profiles: distinctProfiles,
    profiles: ringCoefficientProfiles(dimension, ringType),
    failure_probabilities: failureProbabilities,
  });

  const dfr = {
    ntru: {
      ok: true,
      type: "ntru",
      formula: "p0*(g*s)_n + p1*(f*e)_n + p2*(f*m)_n + p3*e",
      success_condition: "|E| <= Delta",
      dimensions: { n: CYCLIC_NTRU_DFR_DIMENSION },
      delta: "1024",
      precision_bits: 512,
      precision_decimal_digits: 167,
      tail_bits: 128,
      single_coefficient_dfr_log2: "-552.234632750612616122207558",
      vector_dfr_log2_before_ecc: "-543.243110904536920826726148",
      single_coefficient_failure_probability: CYCLIC_NTRU_SINGLE_FAILURE,
      vector_failure_probability_before_ecc: "2.9344632835127240228511998823737693145045E-164",
      single_coefficient_semantics: "worst_coefficient",
      vector_aggregation: "union_bound",
      tail_probability_upper_bound: "0",
      error_support: { size: 3435, minimum: "-2036", maximum: "2036" },
      distributions: {
        g: { support_size: 3, support: ["-1", "1"], tail_probability_upper_bound: "0" },
        f: { support_size: 3, support: ["-1", "1"], tail_probability_upper_bound: "0" },
        s: { support_size: 3, support: ["-1", "1"], tail_probability_upper_bound: "0" },
        e: { support_size: 3, support: ["-1", "1"], tail_probability_upper_bound: "0" },
        m: { support_size: 3, support: ["-1", "1"], tail_probability_upper_bound: "0" },
      },
      coefficients: { p0: "3", p1: "0", p2: "1", p3: "0" },
      ring_type: "cyclic",
      ring_polynomial: "x^509 - 1",
      coefficient_dfr: coefficientDfr(
        CYCLIC_NTRU_DFR_DIMENSION,
        "cyclic",
        0,
        1,
        Array(CYCLIC_NTRU_DFR_DIMENSION).fill(CYCLIC_NTRU_SINGLE_FAILURE),
      ),
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
        s: { support_size: 7, support: ["-3", "3"], tail_probability_upper_bound: "0" },
        e: { support_size: 7, support: ["-3", "3"], tail_probability_upper_bound: "0" },
        e1: { support_size: 5, support: ["-2", "2"], tail_probability_upper_bound: "0" },
        r: { support_size: 7, support: ["-3", "3"], tail_probability_upper_bound: "0" },
        e2: { support_size: 5, support: ["-2", "2"], tail_probability_upper_bound: "0" },
        ec1: { support_size: 5, support: ["-2", "2"], tail_probability_upper_bound: "0" },
        ec2: { support_size: 209, support: ["-104", "104"], tail_probability_upper_bound: "0" },
      },
      warnings: [unionBoundWarning],
      warning_codes: ["dfr_union_bound"],
      error_correction: { included: false, code: "dfr_ecc_external", note: eccNote },
    },
  };

  const BOUNDED_NTRU_DFR_DIMENSION = 64;
  const boundedNtruDistributions = {
    g: {
      support_size: 2,
      support: ["0", "1.0000000000000000000000000000000000000000E+0"],
      tail_probability_upper_bound: "0",
    },
    f: { support_size: 1, support: ["0", "0"], tail_probability_upper_bound: "0" },
    s: {
      support_size: 2,
      support: ["0", "1.0000000000000000000000000000000000000000E+0"],
      tail_probability_upper_bound: "0",
    },
    e: { support_size: 1, support: ["0", "0"], tail_probability_upper_bound: "0" },
    m: { support_size: 1, support: ["0", "0"], tail_probability_upper_bound: "0" },
  };
  const negacyclicFailurePrefix = [
    "1.8634100074649824189873420803188510429700E-11",
    "6.7820207628495972119400109492813030021711E-12",
    "2.3822199683349344036558778054576439151668E-12",
    "8.0594757843160185746438959752757871464319E-13",
    "2.6203905711706144510683720661370221284309E-13",
    "8.1672794201959505007419702492128302208468E-14",
    "2.4334619942007729855555189627653763612989E-14",
    "6.9092673928923931994606038935160974649610E-15",
    "1.8626608401624475654004596089272726285773E-15",
    "4.7482492911235188526845345062478175173799E-16",
    "1.1390749837920500266249210028442886013856E-16",
    "2.5571731467218471451238219574723249886847E-17",
    "5.3368250753861299670406344606064384685832E-18",
    "1.0272461919024845464243746178316265841770E-18",
    "1.8060661361180785946462554151682147099485E-19",
    "2.8657282231205571692649137347273313127478E-20",
    "4.0412474513578076880753258703589948200845E-21",
    "4.9636756137882886858356644061550458309585E-22",
    "5.1648189973665641801103186199706793704323E-23",
    "4.3726077616351190136950197522279381812562E-24",
    "2.8251282483381484772222682385576373192072E-25",
    "1.2388306743465313223900918720453315146065E-26",
    "2.7666193719897721839977237837240654172353E-28",
  ];
  const negacyclicFailures = [
    ...negacyclicFailurePrefix,
    ...Array(17).fill("0"),
    ...negacyclicFailurePrefix.slice().reverse(),
    "4.9501111639157670087038321530746128414527E-11",
  ];
  const ntruPrimeFailures = [
    "4.9501111639157670087038321530746128414527E-11",
    "3.9138111876143930055000198289701273186318E-2",
    "3.4671183459641293610572031870118954873692E-2",
    "3.0606042255205031872785552377165733975429E-2",
    "2.6920314229849487897192477636888147027671E-2",
    "2.3591269561786415919237442387605165268405E-2",
    "2.0596031540656497500047546174158146449771E-2",
    "1.7911774516255914873013977982872293519410E-2",
    "1.5515908742410766743099718770815499168344E-2",
    "1.3386250276770635072064821693431681967396E-2",
    "1.1501174436035952696582952011489703716698E-2",
    "9.8397516611511478910735075460493161059122E-3",
    "8.3818650096852792868088098384833919346248E-3",
    "7.1083088543817618853821773583108604746497E-3",
    "6.0008687193352250145764099842477896398886E-3",
    "5.0423825205814971965690791575733189758849E-3",
    "4.2167837889116195480199918378419165160351E-3",
    "3.5091277331945815635493455637864286933067E-3",
    "2.9056012472377083275443499426700366763252E-3",
    "2.3935181682439977030552627489955222376741E-3",
    "1.9613012575337098365140148424078586747760E-3",
    "1.5984524929868015041090165998404374120961E-3",
    "1.2955133375395384726930056060458302519771E-3",
    "1.0440166801870936918948077998767224209348E-3",
    "8.3643213761047260298200961065777627467775E-4",
    "6.6610635908606555566894442976017943672323E-4",
    "5.2719989893023844912236700067864415140109E-4",
    "4.1462211422878380067938921501779202473479E-4",
    "3.2396541631738137751078664174634674781538E-4",
    "2.5144005798825943897590458312919052627986E-4",
    "1.9381048032606153495492085978357144115399E-4",
    "1.4833407890555162429890649306185842159889E-4",
    "1.1270308397814179739316327789845440627736E-4",
    "8.4990087923489709799807443882473505471726E-5",
    "6.3597599740951256218971361484172459235799E-5",
    "4.7211864111772866242160745179090806799770E-5",
    "3.4761054243006491062648735656949909608235E-5",
    "2.5377835211472411217219395147510392884180E-5",
    "1.8366199012084307596459008832764380387084E-5",
    "1.3172394419944971581080944895915482241087E-5",
    "9.3597138953782679892678342905882012050740E-6",
    "6.5868553320570290134037538503501786334283E-6",
    "4.5895472481321518966893970581480780837371E-6",
    "3.1651104751004565886450495784380528855078E-6",
    "2.1596256941369069594372748868780350985223E-6",
    "1.4573823550512532501493052610265941044373E-6",
    "9.7229860275112297706685234678383165069175E-7",
    "6.4102189386322913203493328339853046276799E-7",
    "4.1744419732983575514096321592861525774948E-7",
    "2.6839239964090683721164983761533845440381E-7",
    "1.7028235559249792920754482910533346992312E-7",
    "1.0655275433028359580316892614156954838010E-7",
    "6.5721667807306447128503845454829027218345E-8",
    "3.9933613161215616386610110284256066484602E-8",
    "2.3887712492536877258320675067010668694718E-8",
    "1.4057791362175127161710930969959434012627E-8",
    "8.1329073931899627199187564457093747521888E-9",
    "4.6218650411987541618196900609685988941512E-9",
    "2.5778779442649050481845528229035462819602E-9",
    "1.4098853174455626975359029725806590749940E-9",
    "7.5535804831008582470864025452532131650085E-10",
    "3.9600974368668675727406464461258686085755E-10",
    "2.0292707254575591507041207809231163095965E-10",
    "1.0150991194647910906445315426348019707389E-10",
  ];
  const boundedNtruDfr = ({
    ringType,
    ringPolynomial,
    singleLog2,
    vectorLog2,
    singleFailure,
    vectorFailure,
    supportSize,
    supportMaximum,
    worstIndex,
    failures,
  }) => {
    const result = clone(dfr.ntru);
    Object.assign(result, {
      dimensions: { n: BOUNDED_NTRU_DFR_DIMENSION },
      delta: "4.0000000000000000000000000000000000000000E+1",
      single_coefficient_dfr_log2: singleLog2,
      vector_dfr_log2_before_ecc: vectorLog2,
      single_coefficient_failure_probability: singleFailure,
      vector_failure_probability_before_ecc: vectorFailure,
      error_support: {
        size: supportSize,
        minimum: "0",
        maximum: supportMaximum,
      },
      distributions: clone(boundedNtruDistributions),
      coefficients: {
        p0: "1.0000000000000000000000000000000000000000E+0",
        p1: "0",
        p2: "0",
        p3: "0",
      },
      ring_type: ringType,
      ring_polynomial: ringPolynomial,
      coefficient_dfr: coefficientDfr(
        BOUNDED_NTRU_DFR_DIMENSION,
        ringType,
        worstIndex,
        BOUNDED_NTRU_DFR_DIMENSION,
        failures,
      ),
    });
    return result;
  };

  const ntruNegacyclicDfr = boundedNtruDfr({
    ringType: "negacyclic",
    ringPolynomial: "x^64 + 1",
    singleLog2: "-34.233748119815360079333096",
    vectorLog2: "-33.115419893542139351128538",
    singleFailure: "4.9501111639157670087038321530746128414527E-11",
    vectorFailure: "1.0746456697085385824488062337146024839633E-10",
    supportSize: 65,
    supportMaximum: "6.4000000000000000000000000000000000000000E+1",
    worstIndex: 63,
    failures: negacyclicFailures,
  });

  const ntruPrimeDfr = boundedNtruDfr({
    ringType: "ntru_prime",
    ringPolynomial: "x^64 - x - 1",
    singleLog2: "-4.675282031475535990241172",
    vectorLog2: "-1.771383474664407200657309",
    singleFailure: "3.9138111876143930055000198289701273186318E-2",
    vectorFailure: "2.9292769910358933776003313430362565313519E-1",
    supportSize: 128,
    supportMaximum: "1.2700000000000000000000000000000000000000E+2",
    worstIndex: 1,
    failures: ntruPrimeFailures,
  });
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
