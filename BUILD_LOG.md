# Build log

Local adoption of seam by the glass lab pipeline. Heavy work is skipped and written down here.

## 2026-10-14 — operations console

- Replaced the introductory workbench layout with a compact operations console: searchable sample queue, status filters, observed counters, sample inspector, measurement form, and keyboard-accessible intake dialog.
- Added a private capture-metadata projection with exact nanosecond strings and no private sample IDs. The evidence screen explicitly separates recorded captures from verified replay.
- Network/schema failures mark state stale and disable writes. Requests are time-bounded, writes are never retried automatically, and connection recovery preserves uncertain-write warnings.
- Extended the installed browser bridge to cover both Glass workflows and the Seam evidence console, including recorded run filtering, actual decisions, timeline search, keyboard tabs, narrow screens, and unchanged evidence file hashes.
- Existing pipeline, durable service, lab, journal, and capture format remain unchanged. The backend is local-only; public-production authentication/deployment is not implied by the UI redesign. No paid inference or hosting.

## 2026-10-01

- Host: this Mac. Python 3.14.2 (`python3`). No extra packages. Seam is a separate checkout on `PYTHONPATH`, not installed.
- Repository: this one, on `main`. No remote at the time of this entry.
- Product: one process, handlers `receive`, `retry`, `reading`, `retest`, `due`. Ports `bench`, `qc`, `report`. Cases: `release`, `busy-then-release`, `fail-then-release`, `overdue`, `dropped`.
- Suite: `python3 -m unittest discover -s tests -t .` from the repository root. 5 tests, 0.325s, OK. Covers the five scripted cases, byte-identical release artifacts, a recording replay with a hidden line past the cutoff, a tape mismatch that does not echo the expected body, a socket leak that faults `socket.getaddrinfo` before any port call, and a live deliver that records three tape lines.
- Release run digest: `7864660de112c64315df7ac8519f8ce9a97df6428a7653059b8a67c2d2ea7cd7`. Case digest: `537131c3bab936bb07a0db40aa68e3c90224edb1d918e55bc2ee06be581f5a82`. Seed 1842 sample id: `smp_8f2a50d0aa7c8057`.
- Skipped: none. No install of seam, no network, no 32 MiB artifact. The socket leak used a 5s subprocess timeout and returned inside it.
