# DejaView Final Contest Polish Design

## Authorization and objective

The user explicitly asked to implement every remaining contest-risk reduction
and verify DejaView as a usable mature product. This work is a new P3.19 release
polish task; it does not reopen or relabel accepted P3.1–P3.18 evidence.

The outcome is a submission-ready engineering package with a meaningful
3–5-minute English demo, a one-command managed local privacy/runtime path, a
machine-executable submission audit, and a fresh browser audit of the current
daily product.

## Constraints

- Preserve the local digital-memory story and the five logical model roles.
- Preserve the accepted six-act live footage and all checksummed P3.1 evidence.
- Never fake a network-cable pull, Radeon execution, benchmark result, or READY
  state.
- Use synthetic demo data only. Do not read `.env` contents or publish machine,
  cloud, account, or credential coordinates.
- Do not stage `third_party/honcho` or QA scratch artifacts.
- Human registration, Rules acknowledgement, official fork/PR creation, and
  final portal submission remain explicit human gates.

## Alternatives considered

1. **Release polish around accepted footage (selected).** Keep the live six-act
   recording intact, add concise architecture and ROCm evidence cards, provide
   English-primary audio/captions, harden runtime lifecycle, and add a release
   checker. This reduces submission risk without re-running accepted GPU work.
2. **Re-record the complete demo.** Visually uniform but needlessly risks the
   accepted Radeon/fallback evidence and consumes the remaining contest window.
3. **Documentation-only extension.** Fast, but padding a frozen frame would not
   demonstrate a more mature product and would leave runtime reproducibility
   dependent on operator knowledge.

## Workstream 1 — English 3–5-minute submission video

The existing 157.2-second live recording remains immutable. A new submission
cut wraps it with useful content rather than idle padding:

- opening: product promise and sovereign-data/stateless-compute boundary;
- architecture: five logical roles and the local privacy gate;
- unchanged six-act live evidence;
- ROCm proof: W7900D/gfx1100, quantization × MTP × concurrency, VRAM and
  throughput evidence provenance;
- closing: verified capabilities, fallback result, and honest submission scope.

The target is 190–220 seconds at 1920×1080, H.264/AAC. English is the primary
language through a clear English narration track and complete burned-in English
captions. The editable SRT remains beside the video. The manifest records exact
duration, streams, size, SHA-256, caption hash, source-footage hash, and an
accurate timeline. A frame montage must prove title-safe captions, useful cards,
and no accidental black/loading/cropped frames.

## Workstream 2 — managed default product runtime

`make product-up` must not require undocumented manual process surgery. It will
use the existing safe launchers and either:

- verify a compatible, already-managed local Sentinel gateway; or
- start a dedicated local `sentinel` + LiteLLM privacy gateway with ownership
  records under the product runtime directory.

The product stack will never adopt or kill an unowned listener. It will fail
early with the exact occupied port and recovery instruction. Rollback and
`product-down` stop only components started and owned by that product runtime.
Local inference logging must not enable detailed request debugging by default.

The live gate starts the current checkout, proves current health schemas, opens
the daily UI, performs a synthetic Radeon recall with a validated citation and
controlled evidence image, checks privacy/profile/status surfaces, and then
stops only the exact managed processes. Existing user data is not reset.

## Workstream 3 — executable submission contract

Add `make submission-check`. It must fail closed when any of these are false:

- required English specification Markdown/DOCX, editable PPTX, captioned MP4,
  SRT, manifest, benchmark report/evidence, licenses, and README links exist;
- the MP4 is 180–300 seconds, 1920×1080, H.264/AAC, and its manifest hashes and
  duration match the file;
- the SRT starts at zero, ends at video duration, has monotonic non-overlapping
  cues, and contains the required six acts plus Radeon and Local Metal evidence;
- public tracked release files contain no ephemeral cloud coordinates,
  credential-like assignments, or tracked secret files;
- the English package and official-fork/PR instructions remain explicit.

The checker reports machine-readable PASS/FAIL rows without reading private
configuration contents. CI calls the same checker. Official GitHub Actions may
be upgraded only to primary-source-verified releases that remove the Node
runtime warning; functional behavior must remain unchanged.

## Workstream 4 — fresh product UX evidence

Capture the actual current daily flow at desktop and mobile widths: status,
timeline, question, citation, evidence drawer, privacy summary, and Honcho
profile. Inspect every saved screenshot before accepting it. Fix only findings
that block the core task, truthful status, readability, keyboard access,
responsive reflow, or evidence trust. Re-run axe and interaction tests; do not
claim complete WCAG conformance from screenshots alone.

## Acceptance

P3.19 can move to `accept` only when all of the following are fresh:

1. The new English-primary video is 180–300 seconds, hashes match, subtitles
   cover the whole cut, audio/video decode, and visual montage inspection passes.
2. `make product-up/status/down` owns the current processes and a synthetic
   current-source Radeon recall/evidence flow passes without leaking paths/OCR.
3. The current UI flow is captured and audited at desktop and mobile widths;
   automated browser and axe checks have zero unresolved serious findings.
4. `make submission-check`, the complete `make test`, clean-checkout setup and
   tests, coordinate/secret scans, JSON/shell/diff checks, and final GitHub CI
   all pass on the exact pushed commit.
5. `docs/verification-log.md`, `STATUS.md`, bilingual README, handbook, licenses,
   video manifest, and TASKBOARD agree. Human-only submission gates remain
   unchecked and visible.

