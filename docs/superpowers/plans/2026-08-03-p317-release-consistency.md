# P3.17 Release Consistency Plan

**Goal:** Turn the accepted DejaView prototype into a clean-clone, fail-closed, competition-ready release without changing its product narrative or five-role model hierarchy.

## Acceptance contract

1. A clean clone has explicit `setup`, `doctor`, `product-up`, `product-down`, and first-party test entry points.
2. Honcho remains pinned at `340175ad`; both repository-owned patches are applied exactly once, and unexpected submodule edits fail closed.
3. Local and Radeon launchers bind gateways to loopback, reject stale/unowned PID files, return non-zero on readiness failure, and never use broad `pkill` cleanup.
4. CI runs the same offline first-party contract as a developer. Hardware/live-cloud checks remain separate and are never faked.
5. Root licensing, English specification, editable presentation, bilingual quickstart, deployment notes, and status all agree with the actual implementation and evidence.
6. All claims distinguish logical roles from simultaneous residency and list unmeasured ROCm benefits as limitations.

## Verification sequence

1. Run release contract tests before implementation and record the expected failures.
2. Implement the smallest release/setup/lifecycle changes that satisfy the contract.
3. Run launcher tests, release tests, all first-party Python/Node suites, shell syntax checks, compose config checks, and document/presentation QA.
4. Run an independent clean-clone/operator audit.
5. Update `docs/verification-log.md`, `STATUS.md`, and `TASKBOARD.json` in the same acceptance commit, verify commit trailers, and push.
