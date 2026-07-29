# 小暖移动端容器

此目录用 Capacitor 把已部署的小暖服务接入 iOS 与 Android。它不是 SDK，也不保存任何模型密钥。

## 生成工程前的必要条件

1. 先将服务部署到 HTTPS 域名，例如 `https://app.example.com`。
2. 在终端设置 `XIAONUAN_APP_URL=https://你的正式域名`。
3. 安装依赖后执行 `npm run sync`，再执行 `npx cap add android` 或 `npx cap add ios`。
4. Android 需要 Android Studio、JDK 和签名证书；iOS 需要 Xcode、Apple 开发者账号与签名证书。

## 发布前必须完成

- 接入原生麦克风权限与稳定的语音识别；不能依赖 WebView 内的浏览器 SpeechRecognition。
- 接入推送通知；系统只可提醒用户进入 App，不能在后台强制唤起页面或持续监听。
- 在首次录音、音色复刻、健康观察和数据共享前分别获得可撤回授权。
- 完成账号注销、数据导出/删除、隐私政策与家属关系绑定。
