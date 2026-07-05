# Scheme Design References

These references are not treated as direct drop-in replacements for the current
bare RLWE estimator. They guide future scheme-level modules, where security,
correctness, encoding, compression, and failure probability must be modeled
together.

## ML-KEM / Kyber

- NIST FIPS 203 specifies ML-KEM, derived from CRYSTALS-Kyber.
- It is based on Module-LWE rather than a single bare RLWE instance.
- Its parameter sets are ML-KEM-512, ML-KEM-768, and ML-KEM-1024; the name
  "512" is a scheme parameter-set name, not simply a single ring degree.
- Useful implementation lessons: CBD sampling, NTT-domain multiplication,
  compression, and explicit decapsulation-failure bounds.

Reference:
https://nvlpubs.nist.gov/nistpubs/fips/nist.fips.203.pdf

## NEV

- NEV is an NTRU encryption/KEM design using vector decoding.
- It is proved under decisional NTRU and RLWE assumptions over
  `Z_q[X]/(X^n+1)`.
- Its main lesson for AILattice is that scheme encoding can radically change
  the feasible modulus and correctness tradeoff; this cannot be captured by a
  bare RLWE hardness estimate alone.

Reference:
https://eprint.iacr.org/2023/1298

## DAWN

- DAWN is an NTRU encryption design using double encoding and zero-divisor
  encoding.
- It targets compact ciphertexts/public-key+ciphertext combinations while
  maintaining low decryption failure.
- Its main lesson for AILattice is that parameter search needs scheme-specific
  correctness/failure scripts in addition to LWE/NTRU hardness estimates.

Reference:
https://link.springer.com/chapter/10.1007/978-981-95-5099-9_13
