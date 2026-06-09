import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "memory_companion.db")
SQLALCHEMY_DATABASE_URL = f"sqlite:///{os.path.abspath(DB_PATH)}"

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
    _auto_migrate()


def _auto_migrate():
    """SQLite 轻量级自动补列：为已存在的表补齐新增字段，避免老库缺列。"""
    from sqlalchemy import inspect, text
    expected_columns = {
        "family_members": {
            "favorite_food": "VARCHAR(200)",
            "favorite_color": "VARCHAR(100)",
            "personality": "VARCHAR(200)",
            "catchphrase": "VARCHAR(200)",
            "special_memory": "TEXT",
        },
        "daily_broadcasts": {
            "member_scripts": "TEXT",
        },
    }
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    with engine.begin() as conn:
        for table, cols in expected_columns.items():
            if table not in existing_tables:
                continue
            current = {c["name"] for c in inspector.get_columns(table)}
            for col_name, col_type in cols.items():
                if col_name not in current:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {col_name} {col_type}'))
