# Gmail OAuth2 配置指南

## 1. 创建 Google Cloud 项目

### 步骤 1：创建项目
1. 访问 [Google Cloud Console](https://console.cloud.google.com/)
2. 点击顶部导航栏的项目下拉菜单
3. 点击"新建项目"
4. 输入项目名称：`DeJob-Mailer`（可自定义）
5. 点击"创建"

### 步骤 2：启用 Gmail API
1. 在左侧菜单选择 **API 和服务** → **库**
2. 搜索 **Gmail API**
3. 点击进入，然后点击"启用"
4. 等待 API 启用完成

### 步骤 3：创建 OAuth 2.0 凭据
1. 左侧菜单选择 **API 和服务** → **凭据**
2. 点击"创建凭据" → **OAuth 客户端 ID**
3. 应用类型选择 **桌面应用**
4. 输入名称：`DeJob Desktop Client`
5. 点击"创建"
6. 记下生成的：
   - **客户端 ID**：以 `.apps.googleusercontent.com` 结尾
   - **客户端密钥**

### 步骤 4：配置 OAuth 同意屏幕
1. 左侧菜单选择 **API 和服务** → **OAuth 同意屏幕**
2. 用户类型选择 **外部**
3. 填写应用信息：
   - 应用名称：`DeJob 招聘聚合器`
   - 用户支持邮箱：您的邮箱
   - 开发者联系信息：您的邮箱
4. 添加作用域：
   - 点击"添加或删除作用域"
   - 手动添加：`https://www.googleapis.com/auth/gmail.send`
   - 点击"更新"
5. 添加测试用户：
   - 点击"添加用户"
   - 添加您的 Gmail 邮箱
6. 完成设置

## 2. 设置本地环境变量

### Windows 命令提示符（临时）
```cmd
set GMAIL_CLIENT_ID=您的客户端ID
set GMAIL_CLIENT_SECRET=您的客户端密钥
```

### Windows PowerShell（临时）
```powershell
$env:GMAIL_CLIENT_ID="您的客户端ID"
$env:GMAIL_CLIENT_SECRET="您的客户端密钥"
```

### 永久设置（推荐）
1. 右键点击"此电脑" → 属性
2. 高级系统设置 → 环境变量
3. 在"用户变量"或"系统变量"中：
   - 点击"新建"
   - 变量名：`GMAIL_CLIENT_ID`
   - 变量值：您的客户端ID
   - 同样方法添加 `GMAIL_CLIENT_SECRET`

## 3. 重启服务器

### 方法 A：使用重启脚本
```cmd
cd D:\JOB
restart_server.bat
```

### 方法 B：手动重启
1. 停止当前服务器：按 **Ctrl+C**
2. 设置环境变量（如果未永久设置）
3. 启动服务器：
   ```cmd
   cd D:\JOB
   python bacnked.py
   ```

## 4. 使用 OAuth2 授权

1. 访问 `http://localhost:5000`
2. 选择职位并上传简历
3. 点击"一键投递"
4. 在配置模态框中：
   - 发送方式选择 **Gmail OAuth2 (推荐)**
   - 点击 **授权Gmail账户**
   - 在 Google 授权页面登录并同意授权
   - 授权后会返回应用
5. 点击"确认发送"开始投递

## 5. 故障排除

### 授权失败
- 确保已添加测试用户邮箱
- 检查重定向 URI：`http://localhost:5000/oauth2callback`
- 确保客户端 ID 和密钥正确

### 令牌过期
- 令牌会自动刷新
- 如需重新授权，删除 `gmail_token.json` 文件

### API 限制
- Gmail API 有每日发送限制
- 免费版本：500封/天
- 如需更多配额，需升级 Google Cloud 项目

## 6. 备用方案
如果 OAuth2 配置复杂，可继续使用 SMTP 授权码方式：
- 发送方式选择 **SMTP授权码**
- 使用 QQ 邮箱或其他支持 SMTP 的邮箱
- 需获取 16 位授权码