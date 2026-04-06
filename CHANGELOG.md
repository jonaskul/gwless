## v0.0.41 — 2026-04-06

- feat: settings close button, button labels, UniFi port field

## v0.0.40 — 2026-04-06

- feat: SSH disabled by default with opt-in checkbox, single password field

## v0.0.39 — 2026-04-06

- feat: move refresh button to far right of header

## v0.0.38 — 2026-04-06

- feat: clicking selected row again closes the side panel

## v0.0.37 — 2026-04-06

- fix: events modal styling, remove header theme btn, settings opens on General, filters stay open

## v0.0.36 — 2026-04-06

- feat: multi-select filters, General settings tab, fix history button

## v0.0.35 — 2026-04-06

- fix: vlan_map not defined in diagnose_api — fetch it there too

## v0.0.34 — 2026-04-06

- feat: fetch VLAN IDs from Sophos and attach to leases

## v0.0.33 — 2026-04-06

- docs: update README — tabbed settings, syslog persistence, new update flow, API table

## v0.0.32 — 2026-04-06

- fix: pre-push hook — skip version bump when no real commits since last bump

## v0.0.31 — 2026-04-06

- (no code changes)

## v0.0.30 — 2026-04-06

- fix: static DHCP reservations — handle nested StaticLease structure

## v0.0.29 — 2026-04-06

- (no code changes)

## v0.0.28 — 2026-04-06

- fix: handle syslog Expire events that have no MAC address (XGS)

## v0.0.27 — 2026-04-06

- (no code changes)

## v0.0.26 — 2026-04-06

- fix: kvRow was escaping HTML in Status field — add raw parameter

## v0.0.25 — 2026-04-06

- (no code changes)

## v0.0.24 — 2026-04-06

- feat: explicit check-for-update flow with remote changelog

## v0.0.23 — 2026-04-06

- (no code changes)

## v0.0.22 — 2026-04-06

- fix: syslog parser — use reported_ip and reported_host (XGS107 field names)

## v0.0.21 — 2026-04-06

- (no code changes)

## v0.0.20 — 2026-04-06

- fix: syslog DHCP parser — broader status matching, better diagnostics

## v0.0.19 — 2026-04-06

- (no code changes)

## v0.0.18 — 2026-04-06

- feat: settings tabs (UniFi first), MAC fallback for hostname, fix Norwegian syslog text

## v0.0.17 — 2026-04-06

- (no code changes)

## v0.0.16 — 2026-04-06

- (no code changes)

## v0.0.15 — 2026-04-06

- feat: persist syslog DHCP leases to SQLite — survive restarts

## v0.0.14 — 2026-04-06

- (no code changes)

## v0.0.13 — 2026-04-06

- (no code changes)

## v0.0.12 — 2026-04-06

- merge: claude/network-dashboard-app-XrHL3 into main

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

