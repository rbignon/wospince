import asyncio
import logging
import os
import queue
import random
import socket
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import aiohttp
import asqlite
import pygame
import twitchio
from pyvidplayer2 import Video
from twitchio import eventsub
from twitchio.ext import commands

try:
    from conf import BOT_ID, CLIENT_ID, CLIENT_SECRET, OWNER_ID
except ImportError:
    print("Rename conf.py.example to conf.py and complete it.")
    sys.exit(1)

LOGGER: logging.Logger = logging.getLogger("Bot")


class Bot(commands.Bot):
    def __init__(
        self, overlay_queue: queue.Queue, *, token_database: asqlite.Pool
    ) -> None:
        self.overlay_queue = overlay_queue
        self.token_database = token_database
        self.uptime = datetime.now()

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
        await self.add_component(Speedrun(self))

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


def formattime(t):
    if t > 60000:
        return f"{time.strftime('%M:%S', time.gmtime(t / 1000))}"
    else:
        return f"{int(t / 1000)}.{t % 1000 // 100}s"


class Speedrun(commands.Component):
    URL = "https://d1qsrp2avfthuv.cloudfront.net/wospins/eldenring/twogods/history-gametime.json"

    def __init__(self, bot: Bot):
        super().__init__()

        self.bot = bot

    @commands.command(aliases=["splits"])
    @commands.cooldown(rate=1, per=30, key=commands.BucketType.channel)
    async def timesaves(self, ctx: commands.Context) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.get(self.URL, params={'version': datetime.now().isoformat()}) as resp:
                # ignore check of Content-Type, as therun.gg returns 'application/octet-stream'.
                doc = await resp.json(content_type=None)

                splits = [
                    {
                        "name": s["name"],
                        "timesave": (
                            int(s["single"]["time"])
                            - int(s["single"]["bestPossibleTime"])
                        ),
                    }
                    for s in doc["splits"]
                    if s["single"]["time"] not in ("", "NaN")
                ]
                splits = sorted(splits, key=lambda s: s["timesave"], reverse=True)[:10]

                timesaves = [
                    f'{s["name"]} ({formattime(s["timesave"])})' for s in splits
                ]

                await ctx.send(f"Timesaves: {', '.join(timesaves)}")


class LaClasse(commands.Component):
    def __init__(self, bot: Bot):
        super().__init__()

        self.bot = bot
        self.sounds = {}

        for kind in os.listdir("sounds"):
            for filename in os.listdir(Path("sounds") / kind):
                self.sounds.setdefault(kind, []).append(filename.split(".")[0])

    @commands.Component.listener()
    async def event_message(self, payload: twitchio.ChatMessage) -> None:
        print(f"[{payload.broadcaster.name}] - {payload.chatter.name}: {payload.text}")

    @commands.command()
    @commands.cooldown(rate=2, per=10, key=commands.BucketType.chatter)
    async def classe(self, ctx: commands.Context) -> None:
        """Affiche la liste des tags"""
        keywords = set()
        while len(keywords) < 5:
            keywords.add(random.choice(self.sounds["lca"]))

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
        if keyword is None:
            await ctx.redemption.refund(token_for=ctx.broadcaster)
            await ctx.send(
                "Tu dois renseigner un mot clef. Utilise la commande !classe."
            )
            return

        keyword = keyword.strip()

        if keyword not in self.sounds["lca"]:
            await ctx.redemption.refund(token_for=ctx.broadcaster)
            await ctx.send(
                f"Déso, mais il n'y a pas de référence à {keyword}. Utilise la commande !classe."
            )
            return

        pygame.mixer.music.load(Path("sounds") / "lca" / f"{keyword}.mp3")
        pygame.mixer.music.play()
        await ctx.redemption.fulfill(token_for=ctx.broadcaster)

    @commands.reward_command(
        id="5b6d55ef-c0cb-4b63-8dca-fb587bf4644f",
        invoke_when=commands.RewardStatus.unfulfilled,
    )
    @commands.cooldown(rate=1, per=30, key=commands.BucketType.chatter)
    async def reward_gl(self, ctx: commands.Context) -> None:
        sound = random.choice(self.sounds["gl"])
        self.bot.overlay_queue.put(
            ("video", str(Path("sounds") / "gl" / f"{sound}.mp4"))
        )
        await ctx.redemption.fulfill(token_for=ctx.broadcaster)

    @reward_gl.error
    async def reward_gl_error(self, error: commands.CommandErrorPayload):
        if isinstance(error.exception, commands.exceptions.CommandOnCooldown):
            await error.context.redemption.refund(token_for=error.context.broadcaster)
            await error.context.send(
                f"{error.context.chatter.mention} évite de spammer les récompenses de chaîne owaCosmique"
            )
            # await self.bot.user.send_whisper(
            #    to_user=error.context.chatter,
            #    message="Yo, évite de spammer les récompenses de chaîne !"
            # )

    @commands.reward_command(
        id="7e4e814b-3623-43a3-b59a-530400be0db7",
        invoke_when=commands.RewardStatus.unfulfilled,
    )
    @commands.cooldown(rate=1, per=30, key=commands.BucketType.chatter)
    async def reward_fail(self, ctx: commands.Context) -> None:
        sound = random.choice(self.sounds["fail"])
        self.bot.overlay_queue.put(
            ("video", str(Path("sounds") / "fail" / f"{sound}.mp4"))
        )
        await ctx.redemption.fulfill(token_for=ctx.broadcaster)

    @reward_gl.error
    async def reward_fail_error(self, error: commands.CommandErrorPayload):
        if isinstance(error.exception, commands.exceptions.CommandOnCooldown):
            await error.context.redemption.refund(token_for=error.context.broadcaster)
            await error.context.send(
                f"{error.context.chatter.mention} évite de spammer les récompenses de chaîne owaCosmique"
            )

    @commands.reward_command(
        id="6091d470-96de-43ed-9be6-fcf17d145e81",
        invoke_when=commands.RewardStatus.unfulfilled,
    )
    @commands.cooldown(rate=1, per=30, key=commands.BucketType.chatter)
    async def reward_gg(self, ctx: commands.Context) -> None:
        sound = random.choice(self.sounds["gg"])
        self.bot.overlay_queue.put(
            ("video", str(Path("sounds") / "gg" / f"{sound}.mp4"))
        )
        await ctx.redemption.fulfill(token_for=ctx.broadcaster)

    @reward_gl.error
    async def reward_gg_error(self, error: commands.CommandErrorPayload):
        if isinstance(error.exception, commands.exceptions.CommandOnCooldown):
            await error.context.redemption.refund(token_for=error.context.broadcaster)
            await error.context.send(
                f"{error.context.chatter.mention} évite de spammer les récompenses de chaîne owaCosmique"
            )

    @commands.command()
    @commands.is_owner()
    async def create_reward(self, ctx: commands.Context) -> None:
        """Create a new reward"""
        name = f"New reward {random.randint(0, 99999)}"
        resp = await ctx.broadcaster.create_custom_reward(name, cost=10)
        await ctx.send(f"Created {name}: {resp.id}")

    @commands.command()
    @commands.is_owner()
    async def who(self, ctx: commands.Context) -> None:
        await ctx.send(f"{socket.gethostname()} ({self.bot.uptime})")

    @commands.command()
    @commands.is_owner()
    async def kill(self, ctx: commands.Context, who: str | None = None) -> None:
        """Kill an instance of the bot"""
        if socket.gethostname() == who:
            await ctx.send(f"{who} has been killed.")
            await self.bot.close()

    @commands.command()
    @commands.is_owner()
    async def killall(self, ctx: commands.Context) -> None:
        """Kill all bot instances"""
        await ctx.send(f"{socket.gethostname()} has been killed.")
        await self.bot.close()


class Overlay:
    FPS = 30
    FramePerSec = pygame.time.Clock()

    def __init__(self, event_queue):
        self.event_queue = event_queue
        self.running = True
        self.screen = None
        self.videos_queue = []
        self.video = None

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
            case "video":
                print("new video", args)
                self.videos_queue.append(args[0])
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
        self.screen = pygame.display.set_mode((1280, 720))

        while self.running:
            self.handle_bot_events()

            for event in pygame.event.get():
                self.handle_screen_event(event)

            self.draw_window()
            self.FramePerSec.tick(self.FPS)

        self.exit()

    def draw_window(self):
        black = 45, 30, 45
        self.screen.fill(black)

        if self.video is None:
            try:
                self.video = Video(self.videos_queue.pop(0), use_pygame_audio=True)
            except IndexError:
                pass

        if self.video is not None:
            try:
                self.video.draw(self.screen, (0, 0))
            except EOFError:
                logging.exception("Unable to draw video")
                self.video.active = False

            if not self.video.active:
                self.video.close()
                self.video = None

        pygame.display.update()


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
