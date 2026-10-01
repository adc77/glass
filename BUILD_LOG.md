# Build log

Local adoption of seam by the glass lab pipeline. Heavy work is skipped and written down here.

## 2026-10-01

- Host: this Mac. Python 3.14.2 (`python3`). No extra packages. Seam is a separate checkout on `PYTHONPATH`, not installed.
- Repository: this one, on `main`. No remote at the time of this entry.
- Product: one process, handlers `receive`, `retry`, `reading`, `retest`, `due`. Ports `bench`, `qc`, `report`. Cases: `release`, `busy-then-release`, `fail-then-release`, `overdue`, `dropped`.
- Suite: `python3 -m unittest discover -s tests -t .` from the repository root. 5 tests, 0.325s, OK. Covers the five scripted cases, byte-identical release artifacts, a recording replay with a hidden line past the cutoff, a tape mismatch that does not echo the expected body, a socket leak that faults `socket.getaddrinfo` before any port call, and a live deliver that records three tape lines.
- Release run digest: `7864660de112c64315df7ac8519f8ce9a97df6428a7653059b8a67c2d2ea7cd7`. Case digest: `537131c3bab936bb07a0db40aa68e3c90224edb1d918e55bc2ee06be581f5a82`. Seed 1842 sample id: `smp_8f2a50d0aa7c8057`.
- Skipped: none. No install of seam, no network, no 32 MiB artifact. The socket leak used a 5s subprocess timeout and returned inside it.
