import asyncio
import logging
import os
import random
import sqlite3
import sys

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


class Bot(commands.Bot):
    rewards = {}

    @classmethod
    def reward(cls, id_: str):
        def inner(func):
            cls.rewards[id_] = func

            return func

        return inner

    def __init__(self, *, token_database: asqlite.Pool) -> None:
        self.token_database = token_database
        super().__init__(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            bot_id=BOT_ID,
            owner_id=OWNER_ID,
            prefix="!",
            scopes=twitchio.Scopes.all(),
        )

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
    def __init__(self, bot):
        super().__init__()

        self.bot = bot
        self.sounds = []

        for filename in os.listdir("sounds"):
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
            f'Tiens, tu peux utiliser la récompense de chaîne avec un mot clef parmi ceux-ci : {", ".join(keywords)}'
        )

    @commands.Component.listener()
    async def event_stream_online(self, payload: twitchio.StreamOnline) -> None:
        # Event dispatched when a user goes live from the subscription we made above...

        # Keep in mind we are assuming this is for ourselves
        # others may not want your bot randomly sending messages...
        await payload.broadcaster.send_message(
            message=f"OMG {payload.broadcaster} est live !",
        )

    @commands.Component.listener()
    async def event_custom_redemption_add(
        self, payload: twitchio.ChannelPointsAutoRedeemAdd
    ) -> None:
        if payload.reward.id in Bot.rewards:
            await Bot.rewards[payload.reward.id](self, payload)

    @Bot.reward("d138869e-77d1-4fc6-8ae7-5106817b7747")
    async def play_sound(self, payload: twitchio.ChannelPointsAutoRedeemAdd) -> None:
        keyword = payload.user_input
        if keyword in self.sounds:
            pygame.mixer.music.load(f"sounds/{keyword}.mp3")
            pygame.mixer.music.play()
        else:
            await payload.broadcaster.send_message(
                sender=self.bot.bot_id,
                message=f"Déso, mais il n'y a pas de référence à {keyword}. Utilise la commande !classe.",
            )


def main() -> None:
    twitchio.utils.setup_logging(level=logging.DEBUG)

    async def runner() -> None:
        pygame.mixer.init()
        async with asqlite.create_pool("tokens.db") as tdb, Bot(
            token_database=tdb
        ) as bot:
            await bot.setup_database()
            await bot.start()

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        LOGGER.warning("Shutting down due to KeyboardInterrupt...")


if __name__ == "__main__":
    main()
