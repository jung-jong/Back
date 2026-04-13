import asyncio


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    loop = asyncio.SelectorEventLoop()
    asyncio.set_event_loop(loop)
    return loop
