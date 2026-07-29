# 小暖

面向长者的语音数字陪伴与家属照护协同产品。

## 本地启动

```bash
cp .env.example .env
pip install -r requirements.txt
python run.py
```

访问 `http://localhost:8000/family`（家属端）或 `http://localhost:8000/play?demo=1`（老人端演示）。

## GitHub / Vercel

- 可直接导入 Vercel：入口为 `api/index.py`，配置见 `vercel.json`。
- 密钥只能在 Vercel Environment Variables 配置，禁止上传 `.env`。
- Vercel 上必须使用 `DATABASE_URL` 指向托管 PostgreSQL；不能使用 SQLite。
- 上传的照片、录音和生成语音必须接入持久对象存储后再面向真实用户开放。

详细执行单见 [部署与 App 上线执行单](docs/DEPLOYMENT_AND_APP_ROADMAP.md)。
