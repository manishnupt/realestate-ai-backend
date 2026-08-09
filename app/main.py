from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.routers import auth, internal, leads, monitoring, properties, property_import, suggestions

app = FastAPI(title="Cold Call Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(property_import.router)
app.include_router(properties.router)
app.include_router(leads.router)
app.include_router(suggestions.router)
app.include_router(internal.router)
app.include_router(monitoring.router)


@app.get("/health")
async def health():
    db_ok = True
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    return {"status": "ok", "database": "up" if db_ok else "down"}
