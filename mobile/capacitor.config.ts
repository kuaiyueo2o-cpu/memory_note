import type { CapacitorConfig } from '@capacitor/cli';

// 在生成 Android/iOS 工程前，替换为已部署的 HTTPS 域名。
// App 绝不能指向 localhost，也不应保存任何模型密钥。
const config: CapacitorConfig = {
  appId: 'com.xiaonuan.companion',
  appName: '小暖',
  webDir: 'www',
  server: {
    url: process.env.XIAONUAN_APP_URL || 'https://app.example.com',
    cleartext: false,
    allowNavigation: ['app.example.com']
  }
};

export default config;
