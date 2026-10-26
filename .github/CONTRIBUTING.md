# Contributing to Tactiq

[Documentation](../docs/README.md) · [Development](../docs/development/getting-started.md) · [Testing](../docs/development/testing.md)

Keep changes focused, evidence-first, and explicit about limitations.

1. Discuss substantial contract, dependency, or deployment changes before implementation.
2. Preserve deterministic event IDs/sequences, source clocks, ownership checks, and existing response compatibility.
3. Add targeted tests for behavior changes. Run the smallest relevant checks, then the required regression/build gates.
4. Update the relevant documentation; distinguish implemented behavior, work in progress, and verified deployment evidence.
5. Submit a pull request describing behavior, validation, risks, and any unavailable checks. Do not represent missing tests as passed.

Do not commit credentials, local environment files, databases, logs, generated audio/video, detector weights, or private/unauthorized footage. Preserve upstream notices and document provenance of any redistributed asset. Synthetic fixtures must be visibly synthetic.

Cloud operations, billable provider checks, credential rotation, history rewrites, and public deployment require explicit authorization. A contribution must not silently provision resources or modify unrelated applications.

By contributing original material, you agree to its distribution under the repository's [MIT license](../LICENSE); third-party rights remain separate. Report vulnerabilities using the [security policy](SECURITY.md), not public exploit details.
