# 一体化镜像：OpenVPN + 面板 + nginx（nginx 只做静态与反代，和宿主机安装保持同构）
#
# 为什么不用 systemd：容器里没有 PID 1 systemd，而宿主机的落地链路正是
# "socket 激活的 root 助手"。这里不去改代码路径，而是复刻同一套权限模型：
#   * openvpn / nginx 由 entrypoint 直接拉起（容器就是服务管理器）
#   * 面板以非 root 用户 ovpnpanel 运行，靠 sudoers 规则调用同一批助手脚本
# 这样面板代码零改动，容器内外行为一致。
FROM debian:12-slim

ENV OVPN_DOCKER=1 \
    DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      openvpn easy-rsa openssl nginx python3 python3-venv python3-pip \
      curl ca-certificates logrotate iptables iproute2 procps sudo rsync \
 && rm -rf /var/lib/apt/lists/*

# 面板程序（安装脚本打包出的分发包）
COPY panel/ovpnmanager-panel.tar.gz /tmp/panel.tar.gz
COPY docker/nginx.conf /etc/nginx/nginx.conf
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
# 助手必须按**不带 .sh 后缀**的名字安装：面板里的 APPLY_CONFIG_BIN 与 sudoers 规则
# 都指向 /usr/local/lib/ovpnmanager/apply-config 这类路径。直接 COPY *.sh 进去会让
# 镜像构建成功，但运行时"找不到助手"——所有改配置功能静默失效。
COPY templates/ /tmp/templates/

RUN set -eux; \
    cd /tmp/templates; \
    for name in apply-config apply-validate apply-panel-config panel-validate \
                panel-nginx net-control service-control backup-now restore-backup \
                apply-dispatch; do \
      install -m 0755 "$name.sh" "/usr/local/lib/ovpnmanager/$name"; \
    done; \
    rm -rf /tmp/templates; \
    chmod +x /usr/local/bin/entrypoint.sh; \
    useradd --system --create-home --shell /usr/sbin/nologin ovpnpanel; \
    ls -l /usr/local/lib/ovpnmanager/

# 容器里没有 socket 激活，用 sudo 通道：只允许调用那几个固定脚本，且不接受参数
RUN printf '%s\n' \
      'ovpnpanel ALL=(root) NOPASSWD: /usr/local/lib/ovpnmanager/apply-config' \
      'ovpnpanel ALL=(root) NOPASSWD: /usr/local/lib/ovpnmanager/apply-panel-config' \
      'ovpnpanel ALL=(root) NOPASSWD: /usr/local/lib/ovpnmanager/net-control' \
      'ovpnpanel ALL=(root) NOPASSWD: /usr/local/lib/ovpnmanager/service-control' \
      'ovpnpanel ALL=(root) NOPASSWD: /usr/local/lib/ovpnmanager/backup-now' \
      'ovpnpanel ALL=(root) NOPASSWD: /usr/local/lib/ovpnmanager/restore-backup' \
      > /etc/sudoers.d/ovpnmanager \
 && chmod 0440 /etc/sudoers.d/ovpnmanager

VOLUME ["/etc/openvpn", "/opt/ovpnmanager/backend/data", "/var/backups/ovpn-oneclick"]
EXPOSE 1194/udp 5698/tcp

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
