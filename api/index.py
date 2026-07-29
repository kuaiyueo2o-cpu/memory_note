"""Vercel 的 Python Serverless 入口。

所有页面和 API 均由 app.main 中同一个 FastAPI 应用处理；密钥只从
Vercel Project Settings 的 Environment Variables 读取。
"""
from app.main import app

