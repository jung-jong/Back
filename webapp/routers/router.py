from fastapi import APIRouter

from webapp.routers import (
    auth,
    chat,
    courses,
    dashboard,
    documents,
    quests,
)


api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(courses.router)
api_router.include_router(documents.router)
api_router.include_router(chat.router)
api_router.include_router(quests.router)
api_router.include_router(dashboard.router)
