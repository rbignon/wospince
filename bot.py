import asyncio
import logging
import os
import queue
import random
import sqlite3
import sys
import time
from pathlib import Path

import asqlite
import pygame
import twitchio
from twitchio import eventsub
from twitchio.ext import commands

try:
    from conf import BOT_ID, CLIENT_ID, CLIENT_SECRET, OWNER_ID
except ImportError:
    print("Rename conf.py.example to conf.py and complete it.")
    sys.exit(1)

LOGGER: logging.Logger = logging.getLogger("Bot")
FPS = 30
running = True


class Bot(commands.Bot):
    def __init__(
        self, overlay_queue: queue.Queue, *, token_database: asqlite.Pool
    ) -> None:
        self.overlay_queue = overlay_queue
        self.token_database = token_database

        super().__init__(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            bot_id=BOT_ID,
            owner_id=OWNER_ID,
            prefix="!",
            scopes=twitchio.Scopes.all(),
        )

    @classmethod
    async def run(cls, overlay_queue: queue.Queue) -> None:
        async with (
            asqlite.create_pool("tokens.db") as tdb,
            cls(overlay_queue, token_database=tdb) as bot,
        ):
            await bot.setup_database()
            await bot.start()

    async def setup_hook(self) -> None:
        if len(self.tokens) < 2:
            LOGGER.info(
                "To setup the bot, open the browser on: http://localhost:4343/oauth?scopes=%s",
                twitchio.Scopes.all(),
            )
            return

        LOGGER.info("Setting up hooks")

        await self.add_component(LaClasse(self))

        subscription = eventsub.ChatMessageSubscription(
            broadcaster_user_id=OWNER_ID, user_id=BOT_ID
        )
        await self.subscribe_websocket(payload=subscription)

        subscription = eventsub.StreamOnlineSubscription(broadcaster_user_id=OWNER_ID)
        await self.subscribe_websocket(payload=subscription)

        subscription = eventsub.ChannelPointsRedeemAddSubscription(
            broadcaster_user_id=OWNER_ID
        )
        await self.subscribe_websocket(
            payload=subscription, as_bot=False, token_for=OWNER_ID
        )

    async def add_token(
        self, token: str, refresh: str
    ) -> twitchio.authentication.ValidateTokenPayload:
        resp: twitchio.authentication.ValidateTokenPayload = await super().add_token(
            token, refresh
        )

        query = """
        INSERT INTO tokens (user_id, token, refresh)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET
            token = excluded.token,
            refresh = excluded.refresh;
        """

        async with self.token_database.acquire() as connection:
            await connection.execute(query, (resp.user_id, token, refresh))

        LOGGER.info("Added token to the database for user: %s", resp.user_id)
        return resp

    async def load_tokens(self, path: str | None = None) -> None:
        async with self.token_database.acquire() as connection:
            rows: list[sqlite3.Row] = await connection.fetchall(
                """SELECT * from tokens"""
            )

        for row in rows:
            await self.add_token(row["token"], row["refresh"])

    async def setup_database(self) -> None:
        query = """CREATE TABLE IF NOT EXISTS tokens(user_id TEXT PRIMARY KEY, token TEXT NOT NULL, refresh TEXT NOT NULL)"""
        async with self.token_database.acquire() as connection:
            await connection.execute(query)

    async def event_ready(self) -> None:
        LOGGER.info("Successfully logged in as: %s", self.bot_id)


class LaClasse(commands.Component):
    def __init__(self, bot: Bot):
        super().__init__()

        self.bot = bot
        self.sounds = []

        for filename in os.listdir(Path("sounds") / "lca"):
            self.sounds.append(filename.split(".")[0])

    @commands.Component.listener()
    async def event_message(self, payload: twitchio.ChatMessage) -> None:
        print(f"[{payload.broadcaster.name}] - {payload.chatter.name}: {payload.text}")

    @commands.command()
    @commands.cooldown(rate=2, per=10, key=commands.BucketType.chatter)
    async def classe(self, ctx: commands.Context) -> None:
        """Affiche la liste des tags"""
        keywords = set()
        while len(keywords) < 5:
            keywords.add(random.choice(self.sounds))

        await ctx.reply(
            f'Tiens, voici quelques mots clefs que tu peux utiliser avec la récompense de chaîne : {", ".join(keywords)}'
        )

    @commands.Component.listener()
    async def event_stream_online(self, payload: twitchio.StreamOnline) -> None:
        await payload.broadcaster.send_message(
            sender=self.bot.bot_id,
            message=f"OMG {payload.broadcaster} est live !",
        )

    @commands.reward_command(
        id="037bf6eb-228b-4972-a212-8128bb8c8391",
        invoke_when=commands.RewardStatus.unfulfilled,
    )
    async def reward_laclasse(
        self, ctx: commands.Context, *, keyword: str | None = None
    ) -> None:
        if keyword in self.sounds:
            pygame.mixer.music.load(Path("sounds") / "lca" / f"{keyword}.mp3")
            pygame.mixer.music.play()
            await ctx.redemption.fulfill(token_for=ctx.broadcaster)
        else:
            await ctx.redemption.refund(token_for=ctx.broadcaster)
            await ctx.send(
                f"Déso, mais il n'y a pas de référence à {keyword}. Utilise la commande !classe."
            )

    @commands.command()
    @commands.is_owner()
    async def create_reward(self, ctx: commands.Context) -> None:
        resp = await ctx.broadcaster.create_custom_reward(
            f"New reward {random.randint(0, 99999)}", cost=10
        )
        await ctx.send(f"Created your redemption: {resp.id}")


class Overlay:
    def __init__(self, event_queue):
        self.event_queue = event_queue
        self.running = True

    def init(self):
        pygame.init()
        pygame.display.set_caption("wospince")

    def stop(self):
        self.running = False

    def exit(self):
        pygame.quit()

    def handle_bot_events(self):
        try:
            while True:
                event = self.event_queue.get_nowait()
                self.handle_bot_event(*event)
                self.event_queue.task_done()
        except queue.Empty:
            pass
        except queue.ShutDown:
            self.running = False

    def handle_bot_event(self, event_type, *args):
        match event_type:
            case _:
                print("handle_bot_event", event_type, args)

    def handle_screen_event(self, event):
        match event.type:
            case pygame.QUIT | pygame.WINDOWCLOSE:
                print("stop running")
                self.running = False
            case pygame.MOUSEMOTION:
                pass
            case _:
                print("handle_screen_event", event)

    def run(self):
        screen = pygame.display.set_mode((1280, 720))
        current_time = 0

        while self.running:
            self.handle_bot_events()

            for event in pygame.event.get():
                self.handle_screen_event(event)

            last_time, current_time = current_time, time.time()
            # call usually takes  a bit longer than ideal for framerate, so subtract from next wait
            # sleeptime = 1/FPS - delayed = 1/FPS - (now-last-1/FPS)
            # also limit max delay to avoid issues with asyncio.sleep() returning immediately for negative values
            waiting_time = max(
                min(1 / FPS - (current_time - last_time - 1 / FPS), 1 / FPS), 0
            )
            time.sleep(waiting_time)  # tick

            self.draw_window(screen)

        self.exit()

    def draw_window(self, screen):
        black = 0, 0, 0
        screen.fill(black)
        pygame.display.flip()


async def main() -> None:
    twitchio.utils.setup_logging(level=logging.DEBUG)

    event_queue = queue.Queue()

    overlay = Overlay(event_queue)
    overlay.init()

    loop = asyncio.get_event_loop()

    overlay_task = loop.run_in_executor(None, overlay.run)

    try:
        await Bot.run(event_queue)
    finally:
        overlay.stop()
        await asyncio.wait([overlay_task])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        LOGGER.warning("Shutting down due to KeyboardInterrupt...")
