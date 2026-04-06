## v0.0.11 — 2026-04-06

- (no code changes)

## v0.0.10 — 2026-04-06

- feat: update check against GitHub main + disable button if already up to date

## v0.0.9 — 2026-04-06

- (no code changes)

## v0.0.8 — 2026-04-06

- merge: bring main up to date + add version/changelog display in UPDATE section
- fix: correct Sophos SFOS syslog DHCP field names per official docs
- debug: capture raw syslog messages to diagnose DHCP filter mismatch
- feat: add version system (VERSION file + /api/version + Settings footer)
- fix: use correct Sophos SFOS field names for DHCP server parsing
- fix: suppress pip root/PATH warnings in install and update scripts
- fix: update SSE generator to use async wrapper for run_in_executor
- fix: expand Sophos static DHCP reservation parsing to cover SFOS field names
- feat: configure console auto-login in LXC installer
- Merge branch 'claude/network-dashboard-app-XrHL3'
- docs: update README to reflect current feature set
- feat: SQLite device history — first/last seen, event log
- feat: add in-app update from GitHub
- feat: add light mode toggle and live connection log panel

## v0.0.7 — 2026-04-06

- (no code changes)

## v0.0.6 — 2026-04-06

- feat: add /api/version endpoint and version display in Settings footer

## v0.0.5 — 2026-04-06

- fix: pre-push hook grep no-match with set -euo pipefail

## v0.0.4 — 2026-04-06

- (no code changes)

## v0.0.3 — 2026-04-06

- feat: add VERSION file (0.0.2)
- feat: add pre-push hook for auto version bump and CHANGELOG
- feat: embedded UDP syslog receiver for real-time DHCP lease tracking
- fix(security): harden credentials, input validation, and frontend auth
- fix(install): copy gwless.service from within container
- Merge pull request #1 from jonaskul/claude/network-dashboard-app-XrHL3
- fix(install): apt-get update before installing curl in remote path
- Support remote installation via curl in install.sh
- fix(install): download from GitHub when run via curl
- Improve container startup reliability with IP and DNS checks
- fix(install): wait for container network before apt-get
- Add web-based settings UI for credential management
- Build complete Gwless application with in-app settings
- Add Gwless: unified DHCP & network client dashboard
- Add complete Gwless application — DHCP & Network Client Dashboard

## v0.0.2 — 2026-04-06

- feat: embedded UDP syslog receiver for real-time DHCP lease tracking
- feat: configure console auto-login in LXC installer
- feat: add version system (VERSION file + /api/version + Settings footer)
- fix: correct Sophos SFOS syslog DHCP field names per official docs (status/leased_ip/client_host_name)
- fix: use correct Sophos SFOS field names for DHCP server parsing (StaticLease, Gateway, SubnetMask, etc.)
- fix: expand Sophos static DHCP reservation parsing to cover SFOS field names
- fix: update SSE generator to use async wrapper for run_in_executor (fixed "network error" on update)
- fix: suppress pip root/PATH warnings in install and update scripts
- debug: capture raw syslog messages to diagnose DHCP filter mismatch
- feat: SQLite device history — first/last seen, event log
- feat: add in-app update from GitHub
- feat: add light mode toggle and live connection log panel
- feat(security): harden credentials, input validation, and frontend auth

