import os

# Settings are read at import time; give the required fields harmless values.
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://user:pass@localhost:4000/test")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
