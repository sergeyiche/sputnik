from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from apps.api.app.api.routes import admin, chat, gigachat, health, knowledge, suggestions
from apps.api.app.config import settings

app = FastAPI(
    title="Parkinson Support Assistant API",
    description=(
        "RAG-ассистент для поддержки людей с болезнью Паркинсона. "
        "Эндпоинты: /health, /v1/knowledge/status, /v1/chat, /v1/session, "
        "/v1/suggestions/starter"
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(gigachat.router)
app.include_router(knowledge.router)
app.include_router(chat.router)
app.include_router(suggestions.router)
app.include_router(admin.router)


@app.get("/")
async def root() -> dict:
    return {
        "service": "Parkinson Support Assistant API",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
        "endpoints": {
            "knowledge_status": "GET /v1/knowledge/status",
            "chat": "POST /v1/chat",
            "session": "GET /v1/session",
            "starter_suggestions": "GET /v1/suggestions/starter?count=3",
            "admin": "/v1/admin/* (требуется вход)",
            "gigachat_test": "GET /v1/gigachat/auth/test",
        },
    }
