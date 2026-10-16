# firewalld 开通 8000、8010 端口（允许 VPN 客户端访问）

## 目标
- 开放 Web 后端端口 `8000/tcp` 与前端调试端口 `8010/tcp`，允许 VPN 网段 `10.8.0.0/24` 客户端访问。
- 保持其他来源的最小暴露（推荐使用 rich rule 精确放行）。

## 环境假设
- 操作系统：RHEL/CentOS 系 firewalld 环境。
- OpenVPN 服务网段：`10.8.0.0/24`。
- 已安装并启用 firewalld：`systemctl status firewalld`。

## 快速操作
```bash
# 方式一：精确放行（推荐，仅允许 10.8.0.0/24 访问 8000/8010）
firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="10.8.0.0/24" port port="8000" protocol="tcp" accept'
firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="10.8.0.0/24" port port="8010" protocol="tcp" accept'

# 方式二：全局开放端口（如需被公网或其他网段访问）
# firewall-cmd --permanent --add-port=8000/tcp
# firewall-cmd --permanent --add-port=8010/tcp

# 应用配置
firewall-cmd --reload
```

## 验证
```bash
# 查看当前区域规则
firewall-cmd --list-all

# 查看 rich rule 是否生效
firewall-cmd --list-rich-rules
```

常见输出示例（包含 rich rule）：
```
rule family="ipv4" source address="10.8.0.0/24" port port="8000" protocol="tcp" accept
rule family="ipv4" source address="10.8.0.0/24" port port="8010" protocol="tcp" accept
```

## 回滚
```bash
# 移除 rich rule
firewall-cmd --permanent --remove-rich-rule='rule family="ipv4" source address="10.8.0.0/24" port port="8000" protocol="tcp" accept'
firewall-cmd --permanent --remove-rich-rule='rule family="ipv4" source address="10.8.0.0/24" port port="8010" protocol="tcp" accept'

# 或移除已开放端口
# firewall-cmd --permanent --remove-port=8000/tcp
# firewall-cmd --permanent --remove-port=8010/tcp

firewall-cmd --reload
```

## 常见问题排查
- 规则未生效：确认已执行 `firewall-cmd --reload`。
- 服务仍不可达：检查服务本身监听地址（确保监听 0.0.0.0 或对应网卡）以及 SELinux（如开启可临时 `setenforce 0` 测试）。
- 端口被占用：`ss -lntp | grep 8000`。

## 备注
- 若后续更换 VPN 网段，需同步更新 rich rule 中的 `source address`。
- 在生产环境推荐使用方式一，减少不必要的暴露面。
