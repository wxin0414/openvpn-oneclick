#  为什么要使用这个VPN

居家办公或者，在外出差，拨通这个VPN 可以直接连接公司开发环境，不需要再使用 CRT做服务端口转发。



## **四、Windows 客户端配置**

### **4.1 安装 OpenVPN 客户端**

1. 访问 [OpenVPN 官网下载页面](https://openvpn.net/community-downloads/)
2. 下载 **OpenVPN 2.6.x Windows Installer**
3. 运行安装程序，选择默认设置
4. 安装完成后，桌面出现 "OpenVPN GUI" 快捷方式

### **4.2 导入配置文件**

1. 将服务器生成的 `.ovpn` 文件发送到 Windows 电脑
2. 将文件复制到以下目录之一：
   - 所有用户：`C:\Program Files\OpenVPN\config\`
   - 当前用户：`C:\Users\[用户名]\OpenVPN\config\`
3. 或者右键点击系统托盘 OpenVPN 图标 → "Import file..."

### **4.3 建立连接**

1. 右键点击系统托盘 OpenVPN 图标
2. 选择 "Connect"
3. 等待连接建立（图标变绿色）
4. 右键点击图标 → "Show Status" 查看连接详情

### **4.4 验证连接**

在 Windows PowerShell 或 CMD 中执行：

powershell

```bash
# 1. 查看 VPN IP
ipconfig | findstr "10.8"
# 应显示：IPv4 Address. . . . . . . . . . . : 10.8.0.x

# 2. 测试 VPN 网关
ping 10.8.0.1 -n 4

# 3. 测试内网连通性
ping 192.168.1.1 -n 4


# 4. 测试开发服务
# MySQL: 使用 IDE 或工具连接 192.168.1.xxx:3306
# Redis: redis-cli -h 192.168.1.xxx ping
# Git: git clone ssh://git@192.168.1.xxx:/path/to/repo.git
```