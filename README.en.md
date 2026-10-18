# ovpn-oneclick

[中文](README.md) | **English**

[![CI](https://github.com/wxin0414/openvpn-oneclick/actions/workflows/ci.yml/badge.svg)](https://github.com/wxin0414/openvpn-oneclick/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/wxin0414/openvpn-oneclick?display_name=tag)](https://github.com/wxin0414/openvpn-oneclick/releases)
[![License](https://img.shields.io/github/license/wxin0414/openvpn-oneclick)](LICENSE)

One command installs an OpenVPN server **plus** a web control panel. Change configuration
without SSH: the panel renders a candidate config, hands it to a root helper that validates,
backs up, replaces atomically, restarts, and rolls the whole change back if the service does
not come up.

> Unlike "just an install script" projects, the value here is **what happens after install**:
> certificates, client distribution, config changes, rollback, self-check, and read-only
> operator accounts all live in the panel.

---

## Features

- **One-command install**: detect OS, install packages, build PKI, write config, set up NAT, start services, deploy the panel, self-check
- **29 editable server settings**: port, protocol, VPN subnet, pushed DNS, split tunnel routes, ciphers, TLS floor, log level, and more
- **Full client lifecycle**: issue, revoke, export, fixed IP, per-client routes, force disconnect
- **One-time links and QR codes**: hand a colleague a link that works once; only the token hash is stored
- **Observability**: traffic history chart, certificate expiry warnings, 11-item self-check with actionable hints
- **Rollback**: config history with unified diff and one-click restore (which backs up first)
- **Read-only role**: `viewer` can see everything; every write is rejected with 403 by a global middleware
- **Privilege separation**: the panel runs unprivileged with `NoNewPrivileges=1` and obtains root only through systemd socket activation

---

## Install

Debian 12 / Ubuntu 22.04+, root, with **1194/udp** (OpenVPN) and **5698/tcp** (panel) reachable.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/wxin0414/openvpn-oneclick/main/install.sh) --auto
```

<details>
<summary>If your server cannot reach GitHub (common in mainland China)</summary>

Copy the repository to the server and run the installer from there. If
`panel/ovpnmanager-panel.tar.gz` sits next to `install.sh`, the installer uses it and never
downloads the payload. The installer also adds apt timeouts and switches to a domestic mirror
only when the official one is measurably slower (original file backed up first).

</details>

The admin password is printed at the end and also stored in
`/opt/ovpnmanager/backend/.env` (`DEFAULT_ADMIN_PASSWORD`, mode `0600`).

```text
Panel : http://<your-ip>:5698/ovpnmanager/
VPN   : udp://<your-ip>:1194
```

> Remember the cloud **security group** — it is separate from the host firewall.

---

## What the panel manages

| Area | Editable | Notes |
| --- | --- | --- |
| Listen | `port`, `proto` | Rebuilds nginx allow rules and NAT; old port stops working immediately |
| Tunnel | VPN subnet, netmask, `topology` | Changing the subnet rebuilds NAT and nginx rules; fixed IPs must be adjusted |
| Routing | pushed DNS (2), full vs split tunnel + split routes, `block-ipv6` | Split routes are pushed as `push "route"` |
| Crypto | `data-ciphers`, `auth`, `tls-version-min` | Wrong values break handshakes; the UI warns that clients must be re-exported |
| Session | `keepalive`, `reneg-sec`, `inactive`, `max-clients`, `duplicate-cn`, `client-to-client` | — |
| Performance | `mssfix`, `sndbuf`, `rcvbuf` | sysctl and congestion control tuned at install |
| Privileges | `user`, `group` | If the service fails to start, the change is rolled back |
| Logs | `log-append`, `status`, `verb`, `mute` | Both paths must be under `/var/log/openvpn/` |
| DNS leak | `push "block-outside-dns"` | Only meaningful for Windows clients |

Read-only display: `management`, `status-version`, `crl-verify`, `client-config-dir`, TLS mode, `cipher`.

Other pages: clients (create/revoke/export/share with QR and one-time link, per-platform client
downloads), panel settings (port, domain, allowed CIDRs, TLS, admin credentials, backup/restore,
online upgrade, self-check), server settings (the table above plus config history with diff and
rollback), dashboard (online count, traffic chart, certificate expiry, connection events).

---

## Security model

<details>
<summary>Why the panel never uses sudo</summary>

Cloud images often set `NoNewPrivileges=1` on the panel unit and it cannot be turned off from
the unit file, so **any `sudo` inside the panel process is refused by the kernel**:

```text
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
```

All privileged actions therefore go through **systemd socket activation**:

```text
panel -> /run/ovpn-oneclick/apply.sock (0660, group = panel user)
      -> apply.socket (Accept=yes; systemd starts apply@.service as root)
      -> apply-dispatch (Python; dispatches on ACTION= to fixed scripts, takes no arguments)
      -> validate -> back up -> replace atomically -> restart -> wait for readiness -> roll back on failure
```

The helper rejects non-regular files, empty files, anything over 128 KB, NUL bytes, files not
owned by the panel user, group/world-writable files, and any directive outside the managed
block's allow-list. Existing directives may only be retired as comments, never silently dropped.

"Failed to start" is decided by requiring the unit to stay `active` for several consecutive
seconds rather than by trusting the exit code of `systemctl restart`.

</details>

---

## Deliberately not implemented

- **Rebuilding the CA**: invalidates every existing client certificate
- **Rotating the tls-crypt key**: invalidates every existing `.ovpn` (the key is inlined)

Both are all-or-nothing and irreversible, so a button would do more harm than good. Use the
certificate expiry warnings to re-issue early, and config history to roll back mistakes.

---

## Verified on

Debian 12 (kernel 6.1), OpenVPN 2.6, single KVM host, consumer links in mainland China.
CI runs the installer assertions, the helper/panel contract checks and 239 backend assertions.

## Credits

- [hwdsl2/openvpn-install](https://github.com/hwdsl2/openvpn-install) — installer structure and UX reference
- [slc14211421/ovpnmanager](https://github.com/slc14211421/ovpnmanager) — original panel that inspired this one (rewritten here)
- [Nyr/openvpn-install](https://github.com/Nyr/openvpn-install) — the earlier OpenVPN installer

## License

See [LICENSE](LICENSE) and [DISCLAIMER.md](DISCLAIMER.md).
