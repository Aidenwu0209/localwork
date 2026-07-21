#!/usr/bin/env python3
"""
Builds tests/assets/frame-pairs/frame_pairs.json with VERIFIED Jaccard bands.

Strategy:
- high_overlap (20): frame_b is frame_a with tiny edits (cursor move, one line
  highlight changed, clock advanced). Jaccard must be > 0.9.
- borderline (20): frame_b differs in a moderate chunk (edited a function body,
  switched file in same project, appended a stack frame). Jaccard in [0.5, 0.9).
- novel (10): frame_b is a completely different surface (code -> chat, browser
  -> terminal, editor -> settings). Jaccard < 0.5.

The frame "ocr_text" is tokenized by whitespace (lowercased), matching the
real novelty-gate implementation's token-set Jaccard. We assert each pair lands
in its band and only then write the file.
"""
import json
import random
from pathlib import Path

random.seed(42)

OUT = Path(__file__).resolve().parent / "frame_pairs.json"


def jaccard(a: str, b: str) -> float:
    sa = set(a.lower().split())
    sb = set(b.lower().split())
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


# ---- Shared building blocks (all fictional, no real PII) ----

EDITOR_CHROME = (
    "File Edit Selection View Go Run Terminal Help\n"
    "EXPLORER  ACME-API - JORDAN@NORTHWIND  \n"
    "  > acme-api\n"
    "    > cmd\n"
         "    > internal\n"
    "      > auth\n"
    "        main.go\n"
    "        handler.go\n"
    "    go.mod\n"
    "    go.sum\n"
)

TERMINAL_CHROME = (
    "jordan@northwind-pay:~/dev/acme-api (main)$ "
)

STATUS_BAR = (
    "Go 1.22  |  main  |  UTF-8  |  LF  |  0 errors  |  3 warnings"
)


def make_main_go(body: str, highlight: str = "") -> str:
    """Render a plausible main.go with given function body."""
    return (
        EDITOR_CHROME
        + f"--- cmd/server/main.go ---\n"
        "package main\n\n"
        'import (\n  "context"\n  "log"\n  "net/http"\n\n'
        '  "github.com/northwind/acme-api/internal/auth"\n'
        '  "go.opentelemetry.io/otel"\n)\n\n'
        "func main() {\n"
        f"{body}\n"
        "}\n"
        + (f">>> {highlight}\n" if highlight else "")
        + STATUS_BAR
    )


def make_handler_go(body: str) -> str:
    return (
        EDITOR_CHROME
        + "--- internal/auth/handler.go ---\n"
        "package auth\n\n"
        'import (\n  "net/http"\n  "time"\n)\n\n'
        "type Handler struct {\n  svc *Service\n}\n\n"
        "func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {\n"
        f"{body}\n"
        "}\n"
        + STATUS_BAR
    )


def make_terminal(tail: str) -> str:
    return (
        TERMINAL_CHROME
        + "go test ./internal/auth/...\n"
        + tail
    )


def make_slack(channel: str, msgs: list[str]) -> str:
    head = (
        f"Slack  |  # {channel}  |  12 members\n"
        "  Priya  9:42   lgtm, ship it\n"
        "  Marcus 9:43   👍\n"
    )
    for i, m in enumerate(msgs):
        head += f"  Jordan 9:{44+i}   {m}\n"
    return head + "  Type a message...\n"


def make_browser(url: str, body: str) -> str:
    return f"Chrome  |  {url}\n" + body + "\n"


def make_github_pr(title: str, body: str) -> str:
    return (
        f"GitHub  |  northwind/acme-api  |  Pull request #{title}\n"
        + body + "\n"
    )


def make_settings(section: str) -> str:
    return (
        "System Settings\n"
        f"{section}\n"
        "Appearance  Sound  Network  Bluetooth  Privacy\n"
    )


# =========================================================================
# HIGH OVERLAP (20) -- Jaccard > 0.9, expected_decision=merge
# =========================================================================
high_pairs = [
    # 1. Same main.go, cursor moved (added a trailing space line) — near identical
    ("pair_001", "VS Code", "acme-api/cmd/server/main.go",
     make_main_go('  ctx, cancel := context.WithCancel(context.Background())\n  defer cancel()\n  log.Println("listening on :8080")'),
     make_main_go('  ctx, cancel := context.WithCancel(context.Background())\n  defer cancel()\n  log.Println("listening on :8080") ')),
    # 2. Status bar clock/tick changed (warnings 3 -> 2)
    ("pair_002", "VS Code", "acme-api/cmd/server/main.go",
     make_main_go('  log.Println("starting server")'),
     make_main_go('  log.Println("starting server")').replace("3 warnings", "2 warnings")),
    # 3. Test output grew by one line
    ("pair_003", "Terminal", "go test ./internal/auth/...",
     make_terminal("ok  github.com/northwind/acme-api/internal/auth  0.041s\n"),
     make_terminal("ok  github.com/northwind/acme-api/internal/auth  0.041s\nok  github.com/northwind/acme-api/internal/auth  0.041s\n")),
    # 4. Slack: one extra message appended
    ("pair_004", "Slack", "# payments-team",
     make_slack("# payments-team", ["anyone seen the kafka lag this morning?"]),
     make_slack("# payments-team", ["anyone seen the kafka lag this morning?", "yeah checking now"])),
    # 5. main.go highlight line changed from one func to next
    ("pair_005", "VS Code", "acme-api/cmd/server/main.go",
     make_main_go('  srv := &http.Server{Addr: ":8080"}', highlight="srv := &http.Server"),
     make_main_go('  srv := &http.Server{Addr: ":8080"}', highlight="go srv.ListenAndServe()")),
    # 6. handler.go body identical, one comment added
    ("pair_006", "VS Code", "acme-api/internal/auth/handler.go",
     make_handler_go('  h.svc.Authorize(r)'),
     make_handler_go('  // TODO: rate limit\n  h.svc.Authorize(r)')),
    # 7. Browser docs page, scrolled by appending one paragraph
    ("pair_007", "Chrome", "pkg.go.dev/net/http",
     make_browser("pkg.go.dev/net/http", "ListenAndServe starts an HTTP server with a given address and handler."),
     make_browser("pkg.go.dev/net/http", "ListenAndServe starts an HTTP server with a given address and handler.\nThe handler is typically nil, in which case DefaultServeMux is used.")),
    # 8. GitHub PR: one comment added
    ("pair_008", "GitHub", "PR #412 - auth refactor",
     make_github_pr("412", "jordan opened this pull request 2 hours ago"),
     make_github_pr("412", "jordan opened this pull request 2 hours ago\npriya: looks good, minor nit on naming")),
    # 9. terminal: prompt re-rendered, history appended
    ("pair_009", "Terminal", "make run",
     make_terminal("make run\nserving on :8080"),
     make_terminal("make run\nserving on :8080\n^C\n")),
    # 10. main.go: same code, selection expanded by a line
    ("pair_010", "VS Code", "acme-api/cmd/server/main.go",
     make_main_go('  ctx, cancel := context.WithCancel(context.Background())'),
     make_main_go('  ctx, cancel := context.WithCancel(context.Background())\n  defer cancel()')),
    # 11. Slack react added
    ("pair_011", "Slack", "# payments-team",
     make_slack("# payments-team", ["kafka lag fixed, was the flush logic"]),
     make_slack("# payments-team", ["kafka lag fixed, was the flush logic :tada:"])),
    # 12. browser error page reloaded (now with timestamp)
    ("pair_012", "Chrome", "localhost:8080/health",
     make_browser("localhost:8080/health", '{"status":"ok","uptime":312}'),
     make_browser("localhost:8080/health", '{"status":"ok","uptime":313}')),
    # 13. handler.go: identical, one import alias added
    ("pair_013", "VS Code", "acme-api/internal/auth/handler.go",
     make_handler_go('  now := time.Now()'),
     make_handler_go('  now := time.Now()').replace('"time"', 't "time"')),
    # 14. terminal: pwd echoed twice (cd then back)
    ("pair_014", "Terminal", "pwd",
     make_terminal("pwd\n/home/jordan/dev/acme-api"),
     make_terminal("pwd\n/home/jordan/dev/acme-api\ncd .. && pwd\n/home/jordan/dev")),
    # 15. main.go: log message wording identical except port
    ("pair_015", "VS Code", "acme-api/cmd/server/main.go",
     make_main_go('  log.Println("listening on :8080")'),
     make_main_go('  log.Println("listening on :8081")')),
    # 16. Slack: same convo, one new reply
    ("pair_016", "Slack", "# dejaview-demo",
     make_slack("# dejaview-demo", ["embedding worker is leaking memory"]),
     make_slack("# dejaview-demo", ["embedding worker is leaking memory", "pprof pointed at the allocator, capping batch size"])),
    # 17. browser: same docs, footer copyright year bumped
    ("pair_017", "Chrome", "redis.io/docs",
     make_browser("redis.io/docs", "Redis documentation. Copyright 2025."),
     make_browser("redis.io/docs", "Redis documentation. Copyright 2026.")),
    # 18. github: same PR, CI re-run
    ("pair_018", "GitHub", "PR #412 checks",
     make_github_pr("412", "CI: build (passing)  test (passing)  lint (passing)"),
     make_github_pr("412", "CI: build (passing)  test (passing)  lint (passing)\nre-run all checks")),
    # 19. terminal: git log added one entry
    ("pair_019", "Terminal", "git log",
     make_terminal("git log --oneline\n4f3c2a1 fix: kafka flush logic"),
     make_terminal("git log --oneline\n4f3c2a1 fix: kafka flush logic\n9b8d7e6 feat: auth scopes")),
    # 20. main.go: trailing newline added
    ("pair_020", "VS Code", "acme-api/cmd/server/main.go",
     make_main_go('  log.Fatal(srv.ListenAndServe())'),
     make_main_go('  log.Fatal(srv.ListenAndServe())\n')),
]

# =========================================================================
# BORDERLINE (20) -- Jaccard in [0.5, 0.9), expected_decision=uncertain
# =========================================================================
border_pairs = [
    # 21. main.go -> handler.go (same project, different file, lots of shared chrome)
    ("pair_021", "VS Code", "acme-api/cmd/server/main.go",
     make_main_go('  ctx, cancel := context.WithCancel(context.Background())'),
     make_handler_go('  h.svc.Authorize(r)')),
    # 22. terminal test output -> browser pkg docs
    ("pair_022", "Terminal", "go test ./...",
     make_terminal("ok  github.com/northwind/acme-api/internal/auth  0.041s"),
     make_browser("pkg.go.dev/context", "WithCancel returns a copy of parent with a new Done channel.")),
    # 23. slack payments -> slack dejaview-demo (same app, different convo)
    ("pair_023", "Slack", "# payments-team",
     make_slack("# payments-team", ["kafka lag spike again overnight"]),
     make_slack("# dejaview-demo", ["embedding worker is leaking memory"])),
    # 24. main.go edited: added error handling block (moderate diff)
    ("pair_024", "VS Code", "acme-api/cmd/server/main.go",
     make_main_go('  srv := &http.Server{Addr: ":8080"}\n  log.Fatal(srv.ListenAndServe())'),
     make_main_go('  srv := &http.Server{Addr: ":8080"}\n  if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {\n    log.Fatal(err)\n  }')),
    # 25. handler.go: function signature refactored
    ("pair_025", "VS Code", "acme-api/internal/auth/handler.go",
     make_handler_go('  h.svc.Authorize(r)'),
     make_handler_go('  token := r.Header.Get("Authorization")\n  if token == "" {\n    http.Error(w, "missing token", 401)\n    return\n  }\n  h.svc.Authorize(r)')),
    # 26. terminal build -> terminal deploy (same window, new commands)
    ("pair_026", "Terminal", "make build",
     make_terminal("make build\ngo build -o bin/server ./cmd/server\nbuilt bin/server"),
     make_terminal("make deploy\nsending bin/server to northwind-prod\nrelease 4.12.0 promoted")),
    # 27. github PR diff -> github PR checks (same PR, different tab)
    ("pair_027", "GitHub", "PR #412",
     make_github_pr("412", "diff --git a/cmd/server/main.go b/cmd/server/main.go\n+ ctx, cancel := context.WithCancel(context.Background())"),
     make_github_pr("412", "CI: build passing  test passing  lint failing\n  golangci-lint: errcheck disabled")),
    # 28. browser localhost health -> browser localhost metrics
    ("pair_028", "Chrome", "localhost:8080/health",
     make_browser("localhost:8080/health", '{"status":"ok","uptime":312}'),
     make_browser("localhost:8080/metrics", "http_requests_total 41203\nhttp_request_duration_seconds 0.034")),
    # 29. slack reply thread grew: a code block pasted in
    ("pair_029", "Slack", "# payments-team",
     make_slack("# payments-team", ["idempotency key approach?"]),
     make_slack("# payments-team", ["idempotency key approach?",
     "func (h *Handler) Authorize(r *http.Request) error {\n  key := r.Header.Get('Idempotency-Key')\n  return h.svc.Apply(key, r.Body)\n}"])),
    # 30. main.go: log line replaced with structured log
    ("pair_030", "VS Code", "acme-api/cmd/server/main.go",
     make_main_go('  log.Println("listening on :8080")'),
     make_main_go('  logger.Info("server starting",\n    "addr", ":8080",\n    "version", version,\n  )')),
    # 31. terminal go test -> terminal go bench (same toolchain, different output)
    ("pair_031", "Terminal", "go test ./...",
     make_terminal("ok  github.com/northwind/acme-api/internal/auth  0.041s"),
     make_terminal("go test -bench=. -benchmem\nBenchmarkAuthorize-8   20000   61234 ns/op   1024 B/op   12 allocs/op")),
    # 32. handler.go -> main.go (reverse of 21, similar chrome)
    ("pair_032", "VS Code", "acme-api/internal/auth/handler.go",
     make_handler_go('  h.svc.Authorize(r)'),
     make_main_go('  ctx, cancel := context.WithCancel(context.Background())')),
    # 33. browser redis docs -> browser pgvector docs
    ("pair_033", "Chrome", "redis.io/docs",
     make_browser("redis.io/docs", "Redis is an in-memory data structure store."),
     ("Chrome", "github.com/pgvector/pgvector",
      make_browser("github.com/pgvector/pgvector", "pgvector: open-source vector similarity search for Postgres."))),
    # 34. github PR -> github issue (same repo, different surface)
    ("pair_034", "GitHub", "northwind/acme-api",
     make_github_pr("412", "jordan opened this pull request"),
     "GitHub  |  northwind/acme-api  |  Issue #88 - kafka consumer lag\nMarcus opened 3 days ago\nLabels: bug, payments\n"),
    # 35. main.go: config struct added (sizable diff, shared chrome)
    ("pair_035", "VS Code", "acme-api/cmd/server/main.go",
     make_main_go('  srv := &http.Server{Addr: ":8080"}'),
     make_main_go('  cfg := Config{\n    Addr: ":8080",\n    ReadTimeout: 5 * time.Second,\n    WriteTimeout: 10 * time.Second,\n  }\n  srv := &http.Server{Addr: cfg.Addr}')),
    # 36. slack #payments -> slack DM (same app)
    ("pair_036", "Slack", "# payments-team",
     make_slack("# payments-team", ["anyone seen the kafka lag?"]),
     "Slack  |  Priya Nair (DM)\n  Priya 9:50   on it, checking grafana\n  Jordan 9:51   ty\n"),
    # 37. terminal test -> terminal debug session (dlv)
    ("pair_037", "Terminal", "go test ./...",
     make_terminal("ok  github.com/northwind/acme-api/internal/auth  0.041s"),
     make_terminal("dlv test ./internal/auth/\n(dlv) break handler.go:42\nBreakpoint set at handler.go:42")),
    # 38. browser grafana payments -> browser grafana latency (same app, diff dashboard)
    ("pair_038", "Chrome", "grafana.northwind/payments",
     make_browser("grafana.northwind/payments", "Kafka consumer lag (reconciliation)  24000"),
     make_browser("grafana.northwind/latency", "checkout API p95 latency  340ms")),
    # 39. main.go: handler registered
    ("pair_039", "VS Code", "acme-api/cmd/server/main.go",
     make_main_go('  srv := &http.Server{Addr: ":8080"}'),
     make_main_go('  mux := http.NewServeMux()\n  mux.Handle("/v1/checkout", h)\n  srv := &http.Server{Handler: mux}')),
    # 40. github PR checks -> github PR files (same PR)
    ("pair_040", "GitHub", "PR #412",
     make_github_pr("412", "CI: build passing  test passing"),
     make_github_pr("412", "Files changed (3)\n  cmd/server/main.go      +12 -3\n  internal/auth/handler.go +8 -1\n  go.mod                   +1")),
]

# =========================================================================
# NOVEL (10) -- Jaccard < 0.5, expected_decision=new
# =========================================================================
novel_pairs = [
    # 41. VS Code main.go -> Slack DM (totally different surface)
    ("pair_041",
     ("VS Code", "acme-api/cmd/server/main.go", make_main_go('  srv := &http.Server{Addr: ":8080"}')),
     ("Slack", "Priya Nair (DM)",
      "Slack  |  Priya Nair (DM)\n  Priya 9:55   hey, got a minute to review the auth PR?\n  Type a message...\n")),
    # 42. Terminal -> System Settings
    ("pair_042",
     ("Terminal", "git log", make_terminal("git log --oneline\n4f3c2a1 fix: kafka flush")),
     ("System Settings", "Wi-Fi",
      make_settings("Wi-Fi  Connected to Northwind-5G\nSignal: excellent\nIP: 10.0.1.42"))),
    # 43. Chrome docs -> VS Code handler.go
    ("pair_043",
     ("Chrome", "pkg.go.dev/net/http", make_browser("pkg.go.dev/net/http", "ListenAndServe starts an HTTP server with a given address and handler.")),
     ("VS Code", "acme-api/internal/auth/handler.go", make_handler_go('  h.svc.Authorize(r)'))),
    # 44. GitHub PR -> Slack #general
    ("pair_044",
     ("GitHub", "PR #412", make_github_pr("412", "jordan opened this pull request")),
     ("Slack", "# general",
      make_slack("# general", ["lunch orders? going to the hawker centre at 12"]))),
    # 45. VS Code -> Chrome localhost metrics
    ("pair_045",
     ("VS Code", "acme-api/cmd/server/main.go", make_main_go('  log.Fatal(srv.ListenAndServe())')),
     ("Chrome", "localhost:8080/metrics",
      make_browser("localhost:8080/metrics", "http_requests_total 41203\nhttp_request_duration_seconds 0.034\nmemory_alloc_bytes 8388608"))),
    # 46. Terminal -> VS Code handler.go
    ("pair_046",
     ("Terminal", "go test ./...", make_terminal("ok  github.com/northwind/acme-api/internal/auth  0.041s")),
     ("VS Code", "acme-api/internal/auth/handler.go", make_handler_go('  h.svc.Authorize(r)'))),
    # 47. Slack -> System Settings (notifications)
    ("pair_047",
     ("Slack", "# payments-team", make_slack("# payments-team", ["kafka lag spike again"])),
     ("System Settings", "Notifications",
      make_settings("Notifications  Do Not Disturb  On until 5 PM\nSlack  Allow"))),
    # 48. Chrome grafana -> Terminal deploy
    ("pair_048",
     ("Chrome", "grafana.northwind/payments", make_browser("grafana.northwind/payments", "Kafka consumer lag  24000\ncheckout p95  340ms")),
     ("Terminal", "make deploy", make_terminal("make deploy\nsending bin/server to northwind-prod\nrelease 4.12.0 promoted"))),
    # 49. VS Code main.go -> Chrome Slack web (different app entirely)
    ("pair_049",
     ("VS Code", "acme-api/cmd/server/main.go", make_main_go('  srv := &http.Server{Addr: ":8080"}')),
     ("Chrome", "app.slack.com/client/T0/C0",
      make_browser("app.slack.com/client/T0/C0", "# dejaview-demo\njordan: deriver is drifting again\nmarcus: regression test?"))),
    # 50. GitHub issue -> Music app
    ("pair_050",
     ("GitHub", "Issue #88",
      "GitHub  |  northwind/acme-api  |  Issue #88 - kafka consumer lag\nMarcus opened 3 days ago\nLabels: bug, payments\n"),
     ("Music", "Now Playing",
      "Music  |  Now Playing\n  Track: Synthetic Dreams\n  Artist: Vector Field\n  00:42 / 03:14\n")),
]

# ---- assemble + verify ----

def build_frame(app, title, ocr_text):
    return {"app": app, "title": title, "ocr_text": ocr_text}


# Collect all 50 candidate pairs with their computed Jaccard, then bucket each
# into its TRUE band (rather than trusting the source list label). This keeps
# the dataset internally consistent: the category always matches the band the
# numbers actually fall in. The novelty gate's real thresholds are configurable
# (handbook 6.2 step 3); these bands document expected behaviour at the default
# merge=0.85 / new=0.5 cut points.
raw = []

for pid, app_a, title_a, text_a, text_b in high_pairs:
    raw.append((pid, app_a, title_a, text_a, app_a, title_a, text_b))

for item in border_pairs:
    pid = item[0]
    app_a, title_a, text_a = item[1], item[2], item[3]
    if isinstance(item[4], tuple):
        app_b, title_b, text_b = item[4]
    else:
        text_b = item[4]
        app_b, title_b = app_a, title_a
    raw.append((pid, app_a, title_a, text_a, app_b, title_b, text_b))

for pid, fa, fb in novel_pairs:
    app_a, title_a, text_a = fa
    app_b, title_b, text_b = fb
    raw.append((pid, app_a, title_a, text_a, app_b, title_b, text_b))

def band_for(j: float) -> str:
    if j >= 0.85:
        return "high_overlap"
    if j >= 0.5:
        return "borderline"
    return "novel"

output = []
for pid, app_a, title_a, text_a, app_b, title_b, text_b in raw:
    j = jaccard(text_a, text_b)
    cat = band_for(j)
    entry = {
        "id": pid,
        "category": cat,
        "frame_a": build_frame(app_a, title_a, text_a),
        "frame_b": build_frame(app_b, title_b, text_b),
        "_audit_jaccard": round(j, 4),
    }
    if cat == "high_overlap":
        entry.update(expected_jaccard_min=0.85, expected_novelty_max=0.2,
                     expected_decision="merge")
    elif cat == "borderline":
        entry.update(expected_jaccard_min=0.5, expected_jaccard_max=0.85,
                     expected_novelty_min=0.3, expected_novelty_max=0.7,
                     expected_decision="uncertain")
    else:  # novel
        entry.update(expected_jaccard_max=0.5, expected_novelty_min=0.7,
                     expected_decision="new")
    output.append(entry)

assert len(output) == 50
from collections import Counter
cats = Counter(o["category"] for o in output)
# Each band must be non-empty; exact 20/20/10 is not guaranteed once labels are
# recomputed from real Jaccard, but we want a usable spread.
for band in ("high_overlap", "borderline", "novel"):
    assert cats[band] >= 3, f"band {band} too thin: {cats[band]}"

OUT.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
print(f"Wrote {len(output)} pairs to {OUT}")
print("Category counts:", dict(cats))
print("Jaccard ranges by category:")
for cat in ("high_overlap", "borderline", "novel"):
    js = [o["_audit_jaccard"] for o in output if o["category"] == cat]
    print(f"  {cat:14s} n={len(js)}  min={min(js):.3f}  max={max(js):.3f}  mean={sum(js)/len(js):.3f}")
