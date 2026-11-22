"""Own the worker process lifecycle; queue consumers arrive with durable execution."""

import argparse
import asyncio
import logging
import signal
import sys

logger = logging.getLogger("compass_worker")


async def serve() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM)
    loop_handlers: list[int] = []
    for sig in signals:
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows event loops lack add_signal_handler; plain handlers still work.
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
        else:
            loop_handlers.append(sig)
    try:
        logger.info("Worker scaffold ready; no task consumers configured")
        await stop.wait()
    finally:
        for sig in loop_handlers:
            loop.remove_signal_handler(sig)
        logger.info("Worker stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compass worker process scaffold")
    parser.add_argument("--version", action="version", version="compass-worker 0.1.0")
    parser.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(name)s: %(message)s")
    asyncio.run(serve())
