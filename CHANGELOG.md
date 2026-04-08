## v0.3.7 — 2026-04-08

- fix: dynamic range and static reservations missing in DHCP modal

## v0.3.6 — 2026-04-08

- feat: DHCP scope modal for Make static

## v0.3.5 — 2026-04-07

- feat: version check in update.sh before downloading

## v0.3.4 — 2026-04-07

- fix: import Body from fastapi for set_device_name endpoint

## v0.3.3 — 2026-04-07

- feat: custom display name per device

## v0.3.2 — 2026-04-07

- chore: remove debug XML logging from DHCP reservation functions

## v0.3.1 — 2026-04-07

- chore: bump to v0.3.0

## v0.3.0 — 2026-04-07

### Static DHCP Reservation Management
- Create static DHCP reservations directly from the device popover ("⚓ Make static")
- Remove static reservations with one click ("✕ Remove static")
- Expandable form pre-filled with current IP and hostname
- Real-time IP validation — warns if IP is inside the dynamic range or already reserved
- Static reservations always take priority over dynamic leases in the merged view
- "Static" shown in the Lease Expires column for static entries

### Filters & UI
- New lease type filter (Static / Dynamic / No lease)
- Connection type filter replaces the redundant Status filter (Wired / Wireless / No UniFi data)
- "✕ Clear" button resets all filters and search in one click — greyed out when nothing is active
- Scope name resolved for syslog/dynamic leases via subnet prefix lookup

### App Settings
- Restart service button next to Log Level in App settings tab

### Docs
- Added Requirements section (Proxmox PVE 8, Sophos XGS SFOS 22, UniFi v8+)
- Expanded Manual Setup section for non-Proxmox installs (systemd service instructions)
- Syslog setup updated with exact Sophos field values (Facility, Severity, Format)

## v0.2.30 — 2026-04-07

- docs: update syslog setup with correct Sophos field values

## v0.2.29 — 2026-04-07

- docs: expand Manual Setup section for non-Proxmox users

## v0.2.28 — 2026-04-07

- docs: update README for public release

## v0.2.27 — 2026-04-07

- fix: always show Clear button, greyed out when no filters active

## v0.2.26 — 2026-04-07

- feat: add Clear filters button

## v0.2.25 — 2026-04-07

- refactor: replace Status filter with Connection Type filter

## v0.2.24 — 2026-04-07

- feat: show Static in Lease Expires column + lease type filter

## v0.2.23 — 2026-04-07

- fix: translate IP validation warnings to English

## v0.2.22 — 2026-04-07

- feat: real-time IP validation in Make static form

## v0.2.21 — 2026-04-07

- feat: real-time IP validation in Make static form — warns if IP is in dynamic DHCP range or already reserved by another device

## v0.2.20 — 2026-04-07

- fix: send IPLease in compact format to preserve dynamic DHCP range

## v0.2.19 — 2026-04-07

- fix: prevent static reservation within dynamic DHCP range

## v0.2.18 — 2026-04-07

- fix: replace nonexistent _invalidate_caches() with correct cache invalidation

## v0.2.17 — 2026-04-07

- fix: use btn-test class for restart service button to match settings style

## v0.2.16 — 2026-04-07

- fix: correct IPLease format and sanitize hostnames for Sophos Set

## v0.2.15 — 2026-04-07

- fix: use correct Sophos SFOS XML structure for StaticLease

## v0.2.14 — 2026-04-07

- debug: log raw DHCPServer config dict from GET response

## v0.2.13 — 2026-04-07

- debug: log Set request payload for DHCP reservation

## v0.2.12 — 2026-04-07

- fix: static reservations take priority over dynamic leases in merger

## v0.2.11 — 2026-04-07

- feat: remove static DHCP reservation from device popover

## v0.2.10 — 2026-04-07

- fix: send complete DHCPServer config when creating static reservation

## v0.2.9 — 2026-04-07

- feat: restart service button in App settings tab

## v0.2.8 — 2026-04-07

- fix: remove invalid operation="add" attribute from Sophos Set request

## v0.2.7 — 2026-04-07

- fix: use s.lease_type instead of c.lease_type for Make static condition

## v0.2.6 — 2026-04-07

- fix: resolve scope_name for syslog/dynamic leases from server list

## v0.2.5 — 2026-04-07

- feat: Make static DHCP reservation from device popover

## v0.2.4 — 2026-04-07

- feat: "Make static" button in device popover — create Sophos DHCP reservation from the dashboard

## v0.2.3 — 2026-04-07

- feat: pause auto-refresh when browser tab is hidden

## v0.2.2 — 2026-04-07

- docs: add Claude Code disclaimer to README

## v0.2.1 — 2026-04-07

- fix: sign-out button style + version shown immediately after login

## v0.2.0 — 2026-04-07

- feat: optional login page with session auth (24h httpOnly cookie)
- feat: sign-out button in header (visible when auth enabled)
- perf: persistent SQLite connection, WAL mode, hourly event pruning
- perf: reuse authenticated UniFi client across cache refreshes
- fix: XSS hardening — escape event type fallbacks in history/events lists
- fix: security response headers (X-Content-Type-Options, X-Frame-Options, X-XSS-Protection)
- fix: switch OUI source to IEEE CSV (reliable, no auth required)
- fix: OUI update shows actual error message in UI
- fix: version in header shown even when auth is enabled
- feat: cleaner App settings tab with distinct sections
- feat: manual OUI update button in settings
- docs: README updated with login page, auth API, config table

## v0.1.28 — 2026-04-07

- fix: show version in header even when auth is enabled

## v0.1.27 — 2026-04-07

- docs: add login page to README

## v0.1.26 — 2026-04-07

- feat: optional login page with session auth

## v0.1.25 — 2026-04-07

- perf: persistent SQLite connection, WAL mode, periodic pruning, reuse UniFi client

## v0.1.24 — 2026-04-07

- fix: XSS hardening + security response headers

## v0.1.23 — 2026-04-07

- docs: update README for current feature set

## v0.1.22 — 2026-04-07

- fix: switch OUI source to IEEE CSV + cleaner app settings tab

## v0.1.21 — 2026-04-07

- fix: robust OUI download + manual update button in settings

## v0.1.20 — 2026-04-07

- feat: show version number next to logo in header

## v0.1.19 — 2026-04-07

- chore: remove redundant dict() copy in backup_restore

## v0.1.18 — 2026-04-07

- chore: deduplicate v0.1.17 changelog entry

## v0.1.17 — 2026-04-07

- fix: duplicate `const url` in downloadBackup causing SyntaxError — entire script failed to parse, leaving all buttons unresponsive after updates since v0.1.13

## v0.1.16 — 2026-04-07

- fix: atomic config write + startup log showing loaded credentials

## v0.1.15 — 2026-04-07

- fix: backup config before update + remove config.yaml.example as runtime fallback

## v0.1.14 — 2026-04-07

- fix: remove systemctl reboot from update.sh — log error instead + syntax check before restart

## v0.1.13 — 2026-04-07

- feat: include-passwords toggle for backup download

## v0.1.12 — 2026-04-07

- fix: move global CONFIG declaration before first use in backup_restore

## v0.1.11 — 2026-04-07

- feat: Apply/Save buttons in settings + include-passwords option in restore

## v0.1.10 — 2026-04-07

- feat: add ⬡ SVG favicon in accent blue

## v0.1.9 — 2026-04-07

- fix: popover overflow — proper flex scroll containment + re-clamp after async history load

## v0.1.8 — 2026-04-07

- fix: defer popover positioning to rAF so offsetHeight is accurate

## v0.1.7 — 2026-04-07

- feat: replace side panel with mouse-position popover

## v0.1.6 — 2026-04-07

- docs: tidy up README — better structure and flow

## v0.1.5 — 2026-04-07

- fix: version comparison + changelog rendering in update check

## v0.1.4 — 2026-04-07

- fix: add python-multipart dep + reboot fallback in update.sh

## v0.1.3 — 2026-04-07

- feat: Backup & Restore tab in Settings

## v0.1.2 — 2026-04-07

- feat: tteck-style installer with SSH/root password + OS update from web UI

## v0.1.1 — 2026-04-07

- chore: release v0.1.0

## v0.1.0 — 2026-04-07

First minor release. Core features complete:
- UniFi + Sophos SFOS DHCP data merged into a single network dashboard
- Real-time DHCP lease tracking via UDP syslog (no SSH required)
- Persistent DHCP leases across restarts (SQLite)
- VLAN ID extraction from Sophos interface config
- Multi-select filter dropdowns (VLAN, source, status)
- Tabbed Settings (General, UniFi, Sophos, App, Update)
- SSH opt-in with full-admin warning; API-only mode default
- In-app update check against GitHub with remote changelog preview
- Device history tracking (first seen, last seen, IP/hostname changes)
- Light/dark theme, 12/24h clock, EU/US date format preferences
- Security: full HTML escaping of syslog data, no credential leaks in /health

## v0.0.43 — 2026-04-07

- fix: security hardening — XSS in syslog display, error leak in /health

## v0.0.42 — 2026-04-06

- feat: animate side panel open with opacity + slide-in transition

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

