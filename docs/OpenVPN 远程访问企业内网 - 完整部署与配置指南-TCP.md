# **OpenVPN 远程访问企业内网 - 完整部署与配置指南**

## **文档说明**

本文档提供在企业 CentOS 7.9 服务器上部署 OpenVPN，实现开发人员安全远程访问内网开发环境（`192.168.1.0/24`）的完整方案。本指南基于实际生产环境验证，包含**TCP协议优化**，解决UDP连接不稳定问题。

---

## **一、网络架构与规划**

### **1.1 服务器网络配置**
| 组件 | 配置 | 说明 |
|------|------|------|
| **服务器系统** | CentOS 7.9.2009 | VMware 虚拟机 |
| **公网网卡** | ens224 (192.168.31.182) | 连接公网，端口映射入口 |
| **内网网卡** | ens192 (192.168.1.182) | 连接开发内网 (192.168.1.0/24) |
| **VPN 内网段** | 10.8.0.0/24 | 分配给远程客户端的 IP 地址池 |
| **公网映射** | 1.1.1.1:1394/TCP → 192.168.31.182:1194/TCP | 路由器端口转发 |

### **1.2 访问目标**
- ✅ Git 代码仓库 (192.168.1.0/24)
- ✅ MySQL 数据库服务 (3306)
- ✅ Redis 缓存服务 (6379)
- ✅ 其他内网开发测试环境

### **1.3 协议选择：为什么用TCP而不是UDP？**
| 场景 | 推荐协议 | 原因 |
|------|----------|------|
| 企业远程办公 | **TCP** | 连接稳定，穿透性好，适合不稳定网络 |
| 实时游戏/音视频 | UDP | 延迟低，可容忍丢包 |
| 家庭网络/移动网络 | **TCP** | 防火墙友好，自动重传 |
| 稳定内网环境 | UDP | 性能略优 |

---

## **二、服务器端部署**

### **2.1 环境准备与检查**

```bash
# 1. 确认系统版本
cat /etc/redhat-release
# 输出: CentOS Linux release 7.9.2009 (Core)

# 2. 确认内核版本和IP转发
uname -r
sysctl net.ipv4.ip_forward
# 必须为: net.ipv4.ip_forward = 1

# 3. 确认网络接口
ip addr show ens224  # 公网接口: 192.168.31.182
ip addr show ens192  # 内网接口: 192.168.1.182

# 4. 同步时间（证书验证需要）
timedatectl set-timezone Asia/Shanghai
ntpdate time.windows.com
```

### **2.2 安装必要软件包**

```bash
# 1. 安装EPEL仓库和基础工具
yum install -y epel-release
yum install -y wget net-tools tcpdump bind-utils

# 2. 安装OpenVPN及相关组件
yum install -y openvpn easy-rsa openssl openssl-devel lz4 lz4-devel lzo lzo-devel

# 3. 验证安装
rpm -qa | grep -E "openvpn|easy-rsa"
# 应显示: openvpn-2.4.12-1.el7.x86_64, easy-rsa-3.0.8-1.el7.noarch
```

### **2.3 配置证书颁发机构 (CA)**

```bash
# 1. 准备Easy-RSA环境
cp -r /usr/share/easy-rsa/3.0.8/ /etc/openvpn/easy-rsa
cd /etc/openvpn/easy-rsa

# 2. 初始化PKI
./easyrsa init-pki

# 3. 创建vars配置文件
cat > vars << 'EOF'
set_var EASYRSA_REQ_COUNTRY    "CN"
set_var EASYRSA_REQ_PROVINCE   "Jiangsu"
set_var EASYRSA_REQ_CITY       "Nanjing"
set_var EASYRSA_REQ_ORG        "YourCompany"
set_var EASYRSA_REQ_EMAIL      "vpnadmin@yourcompany.com"
set_var EASYRSA_REQ_OU         "IT_Dept"
set_var EASYRSA_KEY_SIZE       2048
set_var EASYRSA_ALGO           rsa
set_var EASYRSA_DIGEST         sha256
EOF

# 4. 构建根CA（交互模式）
./easyrsa build-ca
# 提示: 设置CA密码（至少8位），其他信息可回车用默认值
# 重要: 记录此密码，后续签发证书需要
```

### **2.4 生成服务器证书**

```bash
# 1. 生成服务器证书请求（无密码，便于自动启动）
./easyrsa gen-req server nopass
# 提示Common Name时输入: server

# 2. 签发服务器证书
./easyrsa sign-req server server
# 提示: 输入之前设置的CA密码

# 3. 生成Diffie-Hellman参数（耗时5-15分钟）
./easyrsa gen-dh

# 4. 生成TLS认证密钥
openvpn --genkey --secret ta.key

# 5. 复制所有文件到OpenVPN目录
mkdir -p /etc/openvpn/server
cp pki/ca.crt /etc/openvpn/server/
cp pki/issued/server.crt /etc/openvpn/server/
cp pki/private/server.key /etc/openvpn/server/
cp pki/dh.pem /etc/openvpn/server/
cp ta.key /etc/openvpn/server/

# 6. 确保私钥权限正确（关键步骤）
chmod 600 /etc/openvpn/server/server.key
chown root:root /etc/openvpn/server/server.key
```

### **2.5 配置OpenVPN服务器（TCP优化版）**

```bash
# 创建主配置文件 /etc/openvpn/server.conf
cat > /etc/openvpn/server.conf << 'EOF'
# ============================================
# OpenVPN TCP服务器配置 - 企业远程访问专用
# ============================================

# 基础设置
port 1194
proto tcp
dev tun
topology subnet
management 127.0.0.1 7505   # 仅本机监听，供管理接口/在线查询使用

# 证书和密钥（使用绝对路径）
ca /etc/openvpn/server/ca.crt
cert /etc/openvpn/server/server.crt
key /etc/openvpn/server/server.key
dh /etc/openvpn/server/dh.pem
tls-auth /etc/openvpn/server/ta.key 0
crl-verify /etc/openvpn/crl.pem     # 吊销列表校验（revoke 后覆盖此文件）

# 加密设置
cipher AES-256-GCM
auth SHA256
tls-version-min 1.2

# 网络设置
server 10.8.0.0 255.255.255.0

# 重要：推送内网路由到客户端
push "route 192.168.1.0 255.255.255.0"

# DNS设置（推送内网DNS服务器）
push "dhcp-option DNS 192.168.1.1"
push "dhcp-option DNS 114.114.114.114"

# 客户端设置
client-to-client
keepalive 10 120
persist-key
persist-tun

# 用户和权限
user nobody
group nobody

# 日志设置
status /var/log/openvpn/openvpn-status.log
status-version 3
log-append /var/log/openvpn/openvpn.log
verb 3
mute 20

# ========== TCP协议优化 ==========
# TCP性能优化
tcp-queue-limit 64
txqueuelen 4000
socket-flags TCP_NODELAY
push "socket-flags TCP_NODELAY"

# 缓冲区优化
sndbuf 393216
rcvbuf 393216
push "sndbuf 393216"
push "rcvbuf 393216"

# MTU优化（避免分包）
tun-mtu 1500
mssfix

# 连接限制
max-clients 50
EOF

# 创建日志目录
mkdir -p /var/log/openvpn
touch /var/log/openvpn/openvpn-{status,log}.log
chown -R nobody:nobody /var/log/openvpn
```

### **2.6 系统内核与网络配置**

```bash
# 1. 启用内核IP转发（永久生效）
echo "net.ipv4.ip_forward = 1" >> /etc/sysctl.conf
sysctl -p

# 2. TCP性能优化（添加以下参数到sysctl.conf）
cat >> /etc/sysctl.conf << 'EOF'

# TCP性能优化
net.ipv4.tcp_slow_start_after_idle = 0
net.ipv4.tcp_keepalive_time = 300
net.ipv4.tcp_keepalive_probes = 5
net.ipv4.tcp_keepalive_intvl = 15
net.ipv4.tcp_rmem = 4096 87380 33554432
net.ipv4.tcp_wmem = 4096 65536 33554432
net.core.rmem_max = 33554432
net.core.wmem_max = 33554432
EOF

sysctl -p
```

### **2.7 防火墙配置（FirewallD）**

```bash
# 1. 启动并启用firewalld
systemctl start firewalld
systemctl enable firewalld

# 2. 配置基础规则
firewall-cmd --permanent --add-port=1194/tcp
firewall-cmd --permanent --add-service=openvpn
firewall-cmd --permanent --add-masquerade

# 3. 配置区域（双网卡环境）
firewall-cmd --permanent --zone=public --add-interface=ens224
firewall-cmd --permanent --new-zone=internal
firewall-cmd --permanent --zone=internal --add-interface=ens192
firewall-cmd --permanent --zone=internal --set-target=ACCEPT

# 4. 关键NAT规则：允许VPN客户端访问内网
firewall-cmd --permanent --direct --add-rule ipv4 nat POSTROUTING 0 -s 10.8.0.0/24 -o ens192 -j MASQUERADE

# 5. 允许VPN隧道流量
firewall-cmd --permanent --direct --add-rule ipv4 filter INPUT 0 -i tun0 -j ACCEPT
firewall-cmd --permanent --direct --add-rule ipv4 filter FORWARD 0 -i tun0 -o ens192 -j ACCEPT
firewall-cmd --permanent --direct --add-rule ipv4 filter FORWARD 0 -i ens192 -o tun0 -m state --state ESTABLISHED,RELATED -j ACCEPT

# 6. 应用配置
firewall-cmd --reload

# 7. 验证配置
echo "=== 防火墙验证 ==="
firewall-cmd --list-all
firewall-cmd --direct --get-all-rules | grep -E "POSTROUTING|tun0|ens192"
```

### **2.8 SELinux配置（可选但建议）**

```bash
# 检查SELinux状态
getenforce

# 如果是Enforcing模式，设置以下规则
setsebool -P openvpn_can_network_connect 1
setsebool -P haproxy_connect_any 1

# 允许OpenVPN端口
semanage port -a -t openvpn_port_t -p tcp 1194
```

### **2.9 启动OpenVPN服务**

```bash
# 1. 启动服务
systemctl start openvpn@server

# 2. 设置开机自启
systemctl enable openvpn@server

# 3. 验证服务状态
systemctl status openvpn@server

# 4. 检查TCP端口监听
ss -tlnp | grep 1194
# 应显示: LISTEN 0 128 0.0.0.0:1194 0.0.0.0:* users:(("openvpn",pid=...))

# 5. 检查tun0接口
ip addr show tun0
# 应显示: inet 10.8.0.1/24

# 6. 查看启动日志
tail -f /var/log/openvpn/openvpn.log
# 成功标志: Initialization Sequence Completed
```

---

## **三、客户端配置生成与管理**

### **3.1 客户端证书生成脚本**

```bash
#!/bin/bash
# 保存为: /etc/openvpn/create_client.sh
# 使用方法: ./create_client.sh 用户名

CLIENT_NAME="$1"
PUBLIC_IP="1.1.1.1"    # 公司公网IP
PUBLIC_PORT="1394"           # 公网映射端口

if [ -z "$CLIENT_NAME" ]; then
    echo "使用方法: $0 客户端名称"
    echo "示例: $0 zhangsan"
    exit 1
fi

cd /etc/openvpn/easy-rsa

echo "正在为用户 $CLIENT_NAME 生成证书..."
./easyrsa gen-req "$CLIENT_NAME" nopass
./easyrsa sign-req client "$CLIENT_NAME"

echo "生成客户端配置文件..."
mkdir -p /etc/openvpn/client-configs

cat > "/etc/openvpn/client-configs/${CLIENT_NAME}.ovpn" << EOF
client
dev tun
proto tcp
remote ${PUBLIC_IP} ${PUBLIC_PORT}
resolv-retry infinite
nobind
persist-key
persist-tun
remote-cert-tls server
cipher AES-256-GCM
auth SHA256
verb 3
key-direction 1

# TCP连接优化
socket-flags TCP_NODELAY
connect-retry 5 30
connect-retry-max infinite
keepalive 10 120

# Windows兼容性优化
route-method exe
route-delay 2
redirect-gateway def1

<ca>
$(cat /etc/openvpn/server/ca.crt)
</ca>

<cert>
$(cat pki/issued/${CLIENT_NAME}.crt)
</cert>

<key>
$(cat pki/private/${CLIENT_NAME}.key)
</key>

<tls-auth>
$(cat /etc/openvpn/server/ta.key)
</tls-auth>
EOF

echo "================================================"
echo "✅ 客户端配置文件已生成"
echo "文件: /etc/openvpn/client-configs/${CLIENT_NAME}.ovpn"
echo "用户: ${CLIENT_NAME}"
echo "连接地址: ${PUBLIC_IP}:${PUBLIC_PORT}"
echo "================================================"
EOF

# 设置脚本权限
chmod +x /etc/openvpn/create_client.sh

# 生成测试客户端
/etc/openvpn/create_client.sh testuser
```

### **3.2 批量创建客户端**

```bash
#!/bin/bash
# 保存为: /etc/openvpn/create_batch_clients.sh
# 从用户列表文件创建多个客户端

USER_LIST="/etc/openvpn/client-list.txt"

# 创建用户列表文件（示例）
cat > "$USER_LIST" << 'EOF'
zhangsan
lisi
wangwu
zhaoliu
EOF

echo "开始批量创建客户端证书..."
while read -r username; do
    if [ -n "$username" ]; then
        /etc/openvpn/create_client.sh "$username"
        echo "----------------------------------------"
    fi
done < "$USER_LIST"

echo "批量创建完成！"
```

### **3.3 证书吊销管理（员工离职）**

```bash
#!/bin/bash
# 保存为: /etc/openvpn/revoke_client.sh
# 吊销客户端证书

CLIENT_NAME="$1"

if [ -z "$CLIENT_NAME" ]; then
    echo "使用方法: $0 客户端名称"
    echo "示例: $0 zhangsan"
    exit 1
fi

cd /etc/openvpn/easy-rsa

echo "正在吊销证书: $CLIENT_NAME"
./easyrsa revoke "$CLIENT_NAME"
# 输入CA密码确认

echo "生成新的证书吊销列表(CRL)..."
./easyrsa gen-crl

echo "更新服务器配置..."
cp pki/crl.pem /etc/openvpn/server/

# 在server.conf中添加CRL验证（如果尚未添加）
if ! grep -q "crl-verify" /etc/openvpn/server.conf; then
    echo "crl-verify /etc/openvpn/server/crl.pem" >> /etc/openvpn/server.conf
fi

echo "重启OpenVPN服务..."
systemctl restart openvpn@server

echo "✅ 证书吊销完成: $CLIENT_NAME"
```

---

## **四、Windows客户端配置**

### **4.1 安装OpenVPN客户端**

1. **下载地址**：[OpenVPN社区版下载](https://openvpn.net/community-downloads/)
2. **版本选择**：OpenVPN 2.6.x Windows Installer (Windows 10/11)
3. **安装步骤**：
   - 运行安装程序
   - 选择默认安装选项
   - 安装完成后需要**重启电脑**（重要！）

### **4.2 导入配置文件**

#### **方法一：手动复制（推荐）**
```powershell
# 将.ovpn文件复制到以下目录之一：
# 所有用户: C:\Program Files\OpenVPN\config\
# 当前用户: C:\Users\%USERNAME%\OpenVPN\config\

# PowerShell命令：
Copy-Item "songliangchao.ovpn" "C:\Program Files\OpenVPN\config\"
```

#### **方法二：通过GUI导入**
1. 右键点击系统托盘OpenVPN图标
2. 选择 "Import file..."
3. 选择你的.ovpn文件

### **4.3 连接VPN**

1. 右键点击系统托盘OpenVPN图标
2. 选择 "Connect"
3. 等待连接建立（图标变绿色）
4. 右键点击图标 → "Show Status" 查看连接详情

### **4.4 连接验证（Windows PowerShell）**

```powershell
# 1. 验证VPN连接
ipconfig | findstr "10.8"
# 应显示: IPv4 Address. . . . . . . . . . . : 10.8.0.x

# 2. 测试内网连通性
Test-NetConnection -ComputerName 10.8.0.1 -Port 1194
Test-NetConnection -ComputerName 192.168.1.1 -Port 22

# 3. 测试开发服务
# MySQL测试
# mysql -h 192.168.1.xxx -u username -p

# 4. 路由表检查
route print | findstr "10.8.0.0"
```

### **4.5 客户端优化配置（高级）**

在.ovpn文件中添加以下优化参数：

```ini
# Windows特定优化
route-method exe
route-delay 2
redirect-gateway def1

# DNS优化
dhcp-option DNS 8.8.8.8
dhcp-option DNS 8.8.4.4

# 断线自动重连
connect-retry 5 30
connect-retry-max infinite

# 日志设置（调试时使用）
verb 4
mute 20
```

---

## **五、路由器/防火墙端口映射**

### **5.1 端口映射配置**
```
外部配置:
- IP地址: 1.1.1.1 (公司固定公网IP)
- 外部端口: 1394 (TCP)
- 协议: TCP

内部配置:
- 内部IP: 192.168.31.182 (VPN服务器ens224地址)
- 内部端口: 1194 (TCP)
- 协议: TCP
- 描述: OpenVPN远程访问
```

### **5.2 验证端口映射**
```bash
# 从外网测试端口连通性（在其他网络执行）
telnet 1.1.1.1 1394
# 或使用在线工具: https://www.yougetsignal.com/tools/open-ports/
```

---

## **六、连接测试与验证**

### **6.1 分步验证流程**

```bash
# ===== 服务器端验证 =====
# 1. 服务状态
systemctl status openvpn@server

# 2. 端口监听
ss -tlnp | grep 1194

# 3. 接口状态
ip addr show tun0

# 4. 路由表
ip route show | grep -E "10.8.0.0|192.168.1.0"

# 5. 防火墙规则
firewall-cmd --direct --get-all-rules | grep -E "10.8.0.0|ens192"

# ===== 客户端验证 =====
# 1. 基础连通性
ping 10.8.0.1
ping 192.168.1.1

# 2. 服务测试
# MySQL: telnet 192.168.1.xxx 3306
# Redis: redis-cli -h 192.168.1.xxx ping
# Git: git ls-remote ssh://git@192.168.1.xxx/path/to/repo.git

# 3. 带宽测试（可选）
# iperf3 -c 192.168.1.xxx -p 5201 -t 10
```

### **6.2 自动化测试脚本**

```bash
#!/bin/bash
# 保存为: /root/vpn_test.sh
echo "=== OpenVPN连接测试报告 ==="
echo "测试时间: $(date)"
echo ""

# 服务器状态
echo "1. 服务器状态:"
systemctl is-active openvpn@server && echo "✅ 服务运行正常" || echo "❌ 服务异常"

# 端口监听
echo -e "\n2. 端口监听:"
if ss -tlnp | grep -q ":1194 "; then
    echo "✅ TCP 1194端口监听正常"
else
    echo "❌ TCP 1194端口未监听"
fi

# 客户端连接
echo -e "\n3. 客户端连接:"
CONNECTIONS=$(cat /var/log/openvpn/openvpn-status.log 2>/dev/null | grep -c "CLIENT_LIST")
echo "当前连接数: $CONNECTIONS"

# 内网连通性
echo -e "\n4. 内网连通性测试:"
ping -c 2 -W 1 192.168.1.1 >/dev/null 2>&1
if [ $? -eq 0 ]; then
    echo "✅ 内网网关可达"
else
    echo "❌ 内网网关不可达"
fi

# 系统资源
echo -e "\n5. 系统资源:"
df -h / | tail -1 | awk '{print "磁盘使用率: "$5}'
free -h | awk 'NR==2 {print "内存使用: "$3"/"$2}'

echo -e "\n=== 测试完成 ==="
```

---

## **七、监控与维护**

### **7.1 实时监控脚本**

```bash
#!/bin/bash
# 保存为: /root/vpn_monitor.sh
while true; do
    clear
    echo "====== OpenVPN 实时监控 ======"
    echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"
    echo ""
    
    # 连接状态
    echo "【连接状态】"
    if systemctl is-active openvpn@server >/dev/null; then
        echo -ne "服务状态: ✅ 运行中\t"
        CONNECTIONS=$(cat /var/log/openvpn/openvpn-status.log 2>/dev/null | grep -c "CLIENT_LIST" || echo "0")
        echo "连接数: $CONNECTIONS"
    else
        echo "服务状态: ❌ 停止"
    fi
    
    # 系统资源
    echo -e "\n【系统资源】"
    df -h / | tail -1 | awk '{print "磁盘: "$5" used"}'
    free -m | awk 'NR==2 {printf "内存: %.1fG/%.1fG (%.1f%%)\n", $3/1024, $2/1024, $3/$2*100}'
    
    # 网络状态
    echo -e "\n【网络状态】"
    ss -tn state established '( dport = :1194 )' | wc -l | awk '{print "活动连接: "$1-1}'
    
    # 最近错误
    echo -e "\n【最近5分钟错误】"
    grep "$(date '+%H:%M' -d '5 minutes ago')" /var/log/openvpn/openvpn.log 2>/dev/null | \
        grep -E "error|fail|timeout|refused" | tail -3
    
    sleep 10
done
```

### **7.2 日志轮转配置**

```bash
# 配置OpenVPN日志轮转
cat > /etc/logrotate.d/openvpn << 'EOF'
/var/log/openvpn/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 0600 nobody nobody
    postrotate
        /bin/kill -HUP $(cat /var/run/openvpn/server.pid 2>/dev/null) 2>/dev/null || true
    endscript
}
EOF

# 测试日志轮转配置
logrotate -d /etc/logrotate.d/openvpn
```

### **7.3 备份策略**

```bash
#!/bin/bash
# 保存为: /root/backup_openvpn.sh
BACKUP_DIR="/backup/openvpn/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

echo "开始备份OpenVPN配置..."

# 备份配置文件
cp -r /etc/openvpn "$BACKUP_DIR/"

# 备份证书和密钥
cp -r /etc/openvpn/easy-rsa/pki "$BACKUP_DIR/"

# 备份日志
cp /var/log/openvpn/*.log "$BACKUP_DIR/" 2>/dev/null

# 备份防火墙规则
if systemctl is-active firewalld >/dev/null; then
    firewall-cmd --runtime-to-permanent
    cp /etc/firewalld/*.xml "$BACKUP_DIR/" 2>/dev/null
else
    iptables-save > "$BACKUP_DIR/iptables-backup.rules"
fi

# 创建备份清单
echo "=== 备份清单 ===" > "$BACKUP_DIR/backup-list.txt"
find "$BACKUP_DIR" -type f | wc -l >> "$BACKUP_DIR/backup-list.txt"
du -sh "$BACKUP_DIR" >> "$BACKUP_DIR/backup-list.txt"

echo "备份完成: $BACKUP_DIR"
echo "文件数量: $(find "$BACKUP_DIR" -type f | wc -l)"
echo "备份大小: $(du -sh "$BACKUP_DIR" | awk '{print $1}')"
```

---

## **八、故障排除**

### **8.1 常见问题及解决方案**

| 问题现象 | 可能原因 | 解决方案 |
|---------|---------|----------|
| 无法连接公网IP | 端口映射错误/防火墙阻止 | 1. 检查路由器端口映射<br>2. 验证 `firewall-cmd --list-ports`<br>3. 测试 `telnet 公网IP 1394` |
| 连接成功但无法访问内网 | NAT规则错误 | 1. 检查 `firewall-cmd --direct --get-all-rules`<br>2. 确认有 `-o ens192 -j MASQUERADE` 规则 |
| 频繁断开连接 | 网络不稳定/TCP超时 | 1. 调整 `keepalive` 参数（10 60）<br>2. 客户端添加 `connect-retry` 参数<br>3. 检查磁盘空间 `df -h /` |
| 证书验证失败 | 时间不同步/证书过期 | 1. 执行 `ntpdate time.windows.com`<br>2. 检查证书有效期 `openssl x509 -in server.crt -dates` |
| 客户端获得IP但无法上网 | DNS问题/路由问题 | 1. 检查 `.ovpn` 文件中的DNS设置<br>2. 客户端执行 `ipconfig /flushdns` |

### **8.2 紧急恢复步骤**

```bash
# 如果VPN完全中断，按此顺序恢复：
# 1. 检查服务状态
systemctl status openvpn@server

# 2. 检查端口占用
ss -tlnp | grep :1194

# 3. 检查防火墙
firewall-cmd --list-ports | grep 1194

# 4. 检查磁盘空间（首要问题）
df -h /

# 5. 重启服务
systemctl restart openvpn@server

# 6. 查看详细错误
journalctl -u openvpn@server -n 30 --no-pager
```

### **8.3 性能调优**

```bash
# 如果连接速度慢，尝试以下优化：
# 1. 调整MTU（在server.conf中）
tun-mtu 1400
mssfix

# 2. 启用压缩（谨慎使用，可能增加CPU负载）
compress lz4-v2

# 3. 调整缓冲区大小
sndbuf 524288
rcvbuf 524288

# 4. 限制客户端数量（防止过载）
max-clients 30
```

---

## **九、安全最佳实践**

### **9.1 证书管理**
- 每个用户使用独立证书
- 定期更新CA证书（建议每年一次）
- 及时吊销离职员工证书
- 保护CA私钥（不存储在服务器上）

### **9.2 访问控制**
```bash
# 限制客户端访问范围
# 在server.conf中添加：
push "route 192.168.1.0 255.255.255.0"
# 而不是推送默认网关

# 限制特定服务访问（通过防火墙）
firewall-cmd --direct --add-rule ipv4 filter FORWARD 0 -i tun0 -o ens192 -p tcp --dport 3306 -j ACCEPT
firewall-cmd --direct --add-rule ipv4 filter FORWARD 0 -i tun0 -o ens192 -p tcp --dport 22 -j ACCEPT
```

### **9.3 监控与审计**
- 启用连接日志
- 定期审计连接记录
- 监控异常登录行为
- 设置磁盘空间告警

### **9.4 定期维护任务**
```bash
# 每周维护任务
1. 检查磁盘空间: df -h /
2. 检查日志文件: tail -100 /var/log/openvpn/openvpn.log
3. 检查连接数: grep -c "CLIENT_LIST" /var/log/openvpn/openvpn-status.log
4. 更新系统: yum update -y
5. 重启服务（可选）: systemctl restart openvpn@server

# 每月维护任务
1. 备份配置文件: /root/backup_openvpn.sh
2. 检查证书有效期: openssl x509 -in server.crt -dates
3. 清理旧日志: find /var/log/openvpn -name "*.log.*" -mtime +30 -delete
```

---

## **十、附录**

### **10.1 配置文件位置汇总**
| 文件 | 路径 | 说明 |
|------|------|------|
| 主配置 | `/etc/openvpn/server.conf` | OpenVPN服务器配置 |
| 证书目录 | `/etc/openvpn/server/` | 服务器证书和密钥 |
| Easy-RSA | `/etc/openvpn/easy-rsa/` | 证书管理工具 |
| 客户端配置 | `/etc/openvpn/client-configs/` | 生成的客户端配置文件 |
| 日志文件 | `/var/log/openvpn/` | 连接日志和状态日志 |

### **10.2 常用命令速查**
```bash
# 服务管理
systemctl start openvpn@server      # 启动
systemctl stop openvpn@server       # 停止
systemctl restart openvpn@server    # 重启
systemctl status openvpn@server     # 状态

# 网络检查
ss -tlnp | grep 1194                # 检查端口监听
ip addr show tun0                   # 检查VPN接口
ping 10.8.0.1                       # 测试VPN网关

# 证书管理
cd /etc/openvpn/easy-rsa
./easyrsa gen-req username nopass   # 生成客户端证书
./easyrsa revoke username           # 吊销证书

# 日志查看
tail -f /var/log/openvpn/openvpn.log           # 实时日志
cat /var/log/openvpn/openvpn-status.log        # 连接状态
journalctl -u openvpn@server -n 50             # 系统日志
```

### **10.3 快速部署检查清单**
- [ ] 确认网络架构（双网卡配置正确）
- [ ] 安装OpenVPN及相关软件包
- [ ] 生成CA和服务器证书
- [ ] 配置 `server.conf`（TCP协议）
- [ ] 配置防火墙和NAT规则
- [ ] 启动OpenVPN服务并验证
- [ ] 生成客户端配置文件
- [ ] 配置路由器端口映射
- [ ] 客户端连接测试
- [ ] 内网服务访问验证

---

## **文档更新记录**

| 版本 | 日期 | 修改内容 | 备注 |
|------|------|----------|------|
| 2.0 | 2025-12-29 | 优化TCP配置，修复已知问题 | 基于实际部署验证 |
| 1.0 | 2025-12-29 | 初始版本 | 基础UDP配置 |

## **技术支持**
如遇部署问题，请提供以下信息：
1. 服务器日志：`tail -50 /var/log/openvpn/openvpn.log`
2. 服务状态：`systemctl status openvpn@server`
3. 网络测试：`ping 10.8.0.1` 和 `ping 192.168.1.1` 结果
4. 防火墙规则：`firewall-cmd --direct --get-all-rules`

---

**部署完成标志**：
1. 客户端能成功连接并获得 `10.8.0.x` IP地址
2. 能 `ping` 通 `10.8.0.1`（VPN网关）
3. 能 `ping` 通 `192.168.1.x`（内网设备）
4. 开发IDE能正常连接内网MySQL/Redis/Git等服务


按照本文档步骤部署，即可为企业开发人员提供稳定可靠的远程内网访问能力。
