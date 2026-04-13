import asyncio
import sys

import uvicorn


def configure_windows_event_loop() -> None:
    if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


if __name__ == "__main__":
    configure_windows_event_loop()
    uvicorn.run(
        "webapp.main:app",
        host="0.0.0.0",
        port=8000,
        loop="core.event_loop:selector_loop_factory",
        reload=False,
    )
