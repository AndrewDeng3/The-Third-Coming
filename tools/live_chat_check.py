"""End-to-end check against a real local Ollama: latency, memory across a restart, action tags.

    .venv\\Scripts\\python tools\\live_chat_check.py [scratch_db_path]

Uses a throwaway database (never your real memory.db).
"""

import asyncio
import os
import sys
import tempfile
import time

from stickfigure.agent.agent import Agent, ollama_embedder
from stickfigure.agent.emotion import Emotion
from stickfigure.agent.memory import Memory
from stickfigure.agent.ollama import Ollama
from stickfigure.agent.persona import Situation
from stickfigure.config import CONFIG

SIT = Situation("sitting", "the top of the 'Visual Studio Code' window")


async def turn(agent: Agent, text: str) -> None:
    t0 = time.perf_counter()
    first = []

    def on_text(t):
        if t and not first:
            first.append(time.perf_counter() - t0)

    reply = await agent.reply(text, SIT, on_text)
    total = time.perf_counter() - t0
    print(f"\nYOU:   {text}\nSTICK: {reply.text}  actions={reply.actions}")
    print(f"       first token {first[0] * 1000 if first else -1:.0f} ms, total {total * 1000:.0f} ms")
    await agent.drain()


async def main(db: str) -> None:
    ollama = Ollama(CONFIG.ollama_url)
    embed = ollama_embedder(ollama, CONFIG.embed_model)
    mem = Memory(db, embed)
    agent = Agent(ollama, mem, Emotion())
    agent.on_action = lambda a: print(f"       -> body action: {a}")
    t0 = time.perf_counter()
    await agent.warm_up()
    print(f"models warm in {time.perf_counter() - t0:.1f}s")
    await turn(agent, "Hi! I'm Sam. I have a cat named Pixel and I'm learning to play the cello.")
    await turn(agent, "What are you up to right now?")
    await turn(agent, "Can you do a little dance for me?")
    print("\nfacts stored:", [f.text for f in mem.all_facts()])
    mem.close()

    print("\n--- restart ---")
    mem2 = Memory(db, embed)
    agent2 = Agent(ollama, mem2, Emotion())
    await turn(agent2, "Do you remember my cat's name? And what instrument am I learning?")
    mem2.close()
    await ollama.close()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(tempfile.mkdtemp(), "check.db")
    asyncio.run(main(path))
