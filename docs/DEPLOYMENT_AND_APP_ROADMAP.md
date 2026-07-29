# 小暖｜部署与 App 上线执行单

## 已完成的工程准备

- FastAPI 可通过 `DATABASE_URL` 切换至 PostgreSQL；本地仍兼容 SQLite。
- 提供 Dockerfile、Docker Compose、健康检查和 CORS 白名单配置。
- 提供 Capacitor 移动端容器骨架；App 只访问 HTTPS 服务，不包含 DeepSeek 或 MiniMax 密钥。
- 提供 Vercel Serverless 入口（`api/index.py`、`vercel.json`）及可公开访问的隐私／声音授权页面模板。

## Vercel 发布步骤

1. 新建一个私有 GitHub 仓库，上传本项目根目录的发布包；绝不上传 `.env`、`memory_companion.db`、录音或生成音频。
2. 在 Vercel Import Git Repository，框架选择 Other；无需填写 Build Command。
3. 在 Vercel 的 Environment Variables 设置 `DEEPSEEK_API_KEY`、`MINIMAX_API_KEY`、`DATABASE_URL`、`CORS_ORIGINS`。密钥不写入 GitHub。
4. 连接托管 PostgreSQL（例如 Vercel Marketplace 的数据库服务），把其连接串填入 `DATABASE_URL`。
5. 为上传照片、录音和生成音频接入持久对象存储后再允许真实用户上传；Vercel Serverless 本地文件是临时的，不能作为用户资料库。
6. 部署后先访问 `/health`、`/privacy`、`/voice-consent`，再进行内测。

## 仍需外部账号或不可由代码代替的事项

1. 购买并配置域名、HTTPS 与云服务器/托管 PostgreSQL。
2. 创建 Apple Developer 与 Google Play 开发者主体，完成实名和签名。
3. 配置推送服务、iOS 证书、Android 签名证书。
4. 发布前完成隐私政策、音色授权书、敏感个人信息单独同意、账号注销流程与商店资料。

## 建议上线节奏

1. 先部署测试环境：`docker compose up -d --build`，仅邀请真实家庭内测。
2. 在老人真实设备验证：语音识别、网络中断、音频打断、紧急风险提示、家属查看日报。
3. 替换移动端为原生语音识别与推送，再打 Android 内测包和 iOS TestFlight。
4. 内测稳定后再申请商店正式上架。

## 重要边界

- 当前“病情趋势”只能称为陪聊观察，不得宣称诊断、治疗或预测。
- 声纹、医疗健康、连续定位等信息属于敏感个人信息；最小化收集、单独授权、可删除。
- 服务端密钥只通过部署环境变量注入，禁止进入 Git、页面、App 包或截图。
