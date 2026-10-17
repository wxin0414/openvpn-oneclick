# ovpn-oneclick

One command installs an OpenVPN server **and** a web control panel on Debian/Ubuntu.
Everything the panel can change goes through a root helper: validate -> back up -> replace
atomically -> restart -> roll back if the service does not come up. SSH is only needed as a
last resort.

> Chinese documentation: [README.md](README.md)

## Quick start

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/wxin0414/openvpn-oneclick/main/install.sh) --auto
# or, if your network cannot reach raw.githubusercontent.com, copy the repo to the server:
bash install.sh --auto
```

Then open `http://<host>:5698/ovpnmanager/`. The admin password is printed at the end of the
install and stored in `/opt/ovpnmanager/backend/.env`.

Docker is also supported: `docker compose up -d --build` (see `docker-compose.yml`).

## What the panel manages

- **Server settings (29 editable, 24 managed directives)**: port, protocol, VPN network,
  topology, pushed DNS, full-tunnel vs split-tunnel and split routes, IPv6 blackhole,
  data-ciphers, auth, `user`/`group`, log/status paths, `reneg-sec`, `tls-version-min`,
  `verb`/`mute`, `inactive`, `mssfix`, `sndbuf`/`rcvbuf`, keepalive, client-to-client,
  max-clients, duplicate-cn, `push "block-outside-dns"`.
- **Clients**: create/revoke/export, optional fixed IP (validated against the VPN subnet),
  per-client routes, force-disconnect, one-time download link with a QR code.
- **Operations**: service start/stop/restart, ip_forward and NAT egress, backup/restore,
  online upgrade, traffic history charts, certificate expiry warnings, config history with
  diff and one-click rollback, and an 11-item self-check page.

## Design notes

- **The panel never writes system files directly.** It renders a candidate config and hands it
  to a root helper through systemd socket activation (`apply.socket` -> `apply@.service`,
  no arguments). The helper is the only component that validates, backs up, replaces and
  restarts. Note: the panel unit has `NoNewPrivileges=1`, so **sudo cannot work inside the
  panel process** — the socket is the channel.
- **Readiness, not exit codes.** After a restart the helper requires the unit to stay `active`
  for several consecutive seconds; otherwise it rolls the whole change back.
- **Managed block.** Editable directives live between `>>> ovpn-oneclick managed >>>` markers.
  Anything outside is preserved verbatim; directives that disappear are retired as comments,
  never silently dropped.

## Requirements

Debian 12 / Ubuntu 22.04+ (x86_64 or arm64), root, and open ports **1194/udp** (OpenVPN) and
**5698/tcp** (panel). A TUN device and `NET_ADMIN` are required in containers.

## Verified environment

Debian 12 (kernel 6.1), OpenVPN 2.6, nginx, single-node KVM. CI runs the installer assertions,
the apply/panel contract checks and 239 backend assertions on every push.

## License

See [LICENSE](LICENSE). Third-party components keep their own licenses:

- [hwdsl2/openvpn-install](https://github.com/hwdsl2/openvpn-install) — the provisioning ideas
  this installer's structure follows.
- [slc14211421/ovpnmanager](https://github.com/slc14211421/ovpnmanager) — the original panel
  this project was inspired by; the panel here is a rewrite.
