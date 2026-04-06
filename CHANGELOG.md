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

