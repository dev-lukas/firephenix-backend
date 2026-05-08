import unittest
from types import SimpleNamespace

from app.config import Config
from app.rankingsystem.bots.discord import aichat
from app.rankingsystem.bots.discord.client_manager import should_handle_ember_message


class FakeBotUser:
    def __init__(self, mentioned=False):
        self.mentioned = mentioned

    def mentioned_in(self, message):
        return self.mentioned


def fake_message(content="", guild_id=None, bot=False, webhook_id=None):
    return SimpleNamespace(
        content=content,
        guild=SimpleNamespace(id=guild_id if guild_id is not None else Config.DISCORD_GUILD_ID),
        author=SimpleNamespace(bot=bot),
        webhook_id=webhook_id,
    )


class EmberTriggerTests(unittest.TestCase):
    def test_bot_mention_triggers(self):
        message = fake_message("hello", guild_id=Config.DISCORD_GUILD_ID)

        self.assertTrue(
            should_handle_ember_message(message, FakeBotUser(mentioned=True), Config.DISCORD_GUILD_ID)
        )

    def test_standalone_wake_word_triggers_case_insensitive(self):
        for content in ("ember hilf mal", "Ember?", "hey EMBER"):
            with self.subTest(content=content):
                message = fake_message(content, guild_id=Config.DISCORD_GUILD_ID)

                self.assertTrue(
                    should_handle_ember_message(message, FakeBotUser(), Config.DISCORD_GUILD_ID)
                )

    def test_wake_word_does_not_match_inside_other_words(self):
        message = fake_message("please remember this", guild_id=Config.DISCORD_GUILD_ID)

        self.assertFalse(
            should_handle_ember_message(message, FakeBotUser(), Config.DISCORD_GUILD_ID)
        )

    def test_bot_webhook_dm_and_other_guild_messages_do_not_trigger(self):
        cases = [
            fake_message("ember", bot=True),
            fake_message("ember", webhook_id=123),
            SimpleNamespace(content="ember", guild=None, author=SimpleNamespace(bot=False)),
            fake_message("ember", guild_id=999),
        ]

        for message in cases:
            with self.subTest(message=message):
                self.assertFalse(
                    should_handle_ember_message(message, FakeBotUser(), Config.DISCORD_GUILD_ID)
                )


def openrouter_model(
    model_id,
    prompt="0",
    completion="0",
    request="0",
    context_length=32000,
    output_modalities=None,
    created=1,
):
    return {
        "id": model_id,
        "pricing": {"prompt": prompt, "completion": completion, "request": request},
        "context_length": context_length,
        "output_modalities": output_modalities or ["text"],
        "created": created,
    }


class OpenRouterModelSelectionTests(unittest.TestCase):
    def setUp(self):
        self.original_min_context = Config.OPENROUTER_MIN_CONTEXT_LENGTH
        self.original_limit = Config.OPENROUTER_MODEL_FALLBACK_LIMIT
        Config.OPENROUTER_MIN_CONTEXT_LENGTH = 16000
        Config.OPENROUTER_MODEL_FALLBACK_LIMIT = 5

    def tearDown(self):
        Config.OPENROUTER_MIN_CONTEXT_LENGTH = self.original_min_context
        Config.OPENROUTER_MODEL_FALLBACK_LIMIT = self.original_limit

    def test_rank_free_models_filters_paid_non_text_and_tiny_models(self):
        ranked = aichat._rank_free_models(
            [
                openrouter_model("google/gemini-2.5-flash:free", context_length=100000),
                openrouter_model("google/gemini-pro:free", context_length=100000),
                openrouter_model("deepseek/deepseek-chat:free", context_length=32000),
                openrouter_model("qwen/qwen3:free", context_length=64000),
                openrouter_model("paid/model", prompt="0.0001"),
                openrouter_model("image/model:free", output_modalities=["image"]),
                openrouter_model("tiny/model:free", context_length=8000),
                {"id": "missing/pricing:free", "context_length": 64000, "output_modalities": ["text"]},
            ]
        )

        self.assertEqual(
            ranked,
            [
                "deepseek/deepseek-chat:free",
                "google/gemini-2.5-flash:free",
                "google/gemini-pro:free",
                "qwen/qwen3:free",
            ],
        )

    def test_select_models_keeps_free_router_as_final_fallback(self):
        selected = aichat._select_openrouter_models(
            [
                "google/gemini-2.5-flash:free",
                "deepseek/deepseek-chat:free",
                "qwen/qwen3:free",
                "meta-llama/llama-3.3:free",
            ]
        )

        self.assertEqual(selected[-1], aichat.OPENROUTER_FREE_ROUTER)
        self.assertEqual(len(selected), 5)


class FakeChannel:
    def __init__(self, messages):
        self.messages = messages
        self.name = "general"

    def history(self, limit=None, before=None):
        async def iterator():
            for message in self.messages[:limit]:
                yield message

        return iterator()


def fake_discord_author(id, display_name, bot=False, roles=None):
    return SimpleNamespace(
        id=id,
        display_name=display_name,
        name=display_name,
        bot=bot,
        roles=roles or [],
        voice=None,
    )


def fake_discord_history_message(author, content):
    return SimpleNamespace(
        author=author,
        content=content,
        clean_content=content,
        attachments=[],
        stickers=[],
        reference=None,
    )


class EmberContextTests(unittest.IsolatedAsyncioTestCase):
    def test_system_prompt_uses_current_public_server_facts(self):
        self.assertIn("TeamSpeak 3: firephenix.de, Port 9987", aichat.SYSTEM_PROMPT)
        self.assertIn("Garry's Mod TTT: firephenix.de:27015", aichat.SYSTEM_PROMPT)
        self.assertIn("Passwort ember", aichat.SYSTEM_PROMPT)
        self.assertIn("Seasons laufen jaehrlich", aichat.SYSTEM_PROMPT)
        self.assertNotIn("gaming.firephenix.de", aichat.SYSTEM_PROMPT)
        self.assertNotIn("ts.firephenix.de", aichat.SYSTEM_PROMPT)

    def test_system_prompt_allows_snippy_but_accurate_replies_to_toxic_users(self):
        self.assertIn("Standardmaessig bist du freundlich", aichat.SYSTEM_PROMPT)
        self.assertIn("aggressiv, toxisch oder unfreundlich", aichat.SYSTEM_PROMPT)
        self.assertIn("snippy kontern", aichat.SYSTEM_PROMPT)
        self.assertIn("sachlich korrekt", aichat.SYSTEM_PROMPT)
        self.assertIn("Keine harten Beleidigungen", aichat.SYSTEM_PROMPT)

    async def test_context_includes_history_with_authors_and_excludes_other_bots(self):
        bot_id = 55
        ember_author = fake_discord_author(bot_id, "Ember", bot=True)
        other_bot = fake_discord_author(99, "OtherBot", bot=True)
        human = fake_discord_author(10, "Lukas")
        channel = FakeChannel(
            [
                fake_discord_history_message(human, "ember was ist mein rang?"),
                fake_discord_history_message(ember_author, "Du bist Level 4."),
                fake_discord_history_message(other_bot, "ignore me"),
            ]
        )
        current = SimpleNamespace(
            channel=channel,
            guild=SimpleNamespace(me=SimpleNamespace(id=bot_id)),
        )

        context = await aichat.build_conversation_context(current)

        self.assertIn("Lukas: ember was ist mein rang?", context)
        self.assertIn("Ember: Du bist Level 4.", context)
        self.assertNotIn("OtherBot", context)

    async def test_fetch_user_info_formats_database_profile(self):
        original_database = aichat.DatabaseManager

        class FakeDatabase:
            def execute_query(self, query, params=None):
                return [(1, "Lukas", "10", "ts-id", 4, 2, 370, 3050)]

            def close(self):
                pass

        aichat.DatabaseManager = FakeDatabase
        try:
            info = await aichat.fetch_user_info_string(10)
        finally:
            aichat.DatabaseManager = original_database

        self.assertIn("Name: Lukas", info)
        self.assertIn("Rang: Level 4", info)
        self.assertIn("Division: Silber", info)
        self.assertIn("Noch", info)

    def test_build_messages_contains_requester_profile_context_and_current_message(self):
        author = fake_discord_author(10, "Lukas", roles=[SimpleNamespace(name="Admin")])
        message = SimpleNamespace(
            author=author,
            content="Ember, wie weit bis Gold?",
            clean_content="Ember, wie weit bis Gold?",
            guild=SimpleNamespace(me=SimpleNamespace(id=55)),
            channel=SimpleNamespace(name="ranking"),
            attachments=[],
            stickers=[],
            reference=None,
        )

        messages = aichat.build_messages(message, "[Benutzer-Info] Rang: Level 4.", "Lukas: hi")

        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("Discord-Name: Lukas", messages[1]["content"])
        self.assertIn("[Benutzer-Info] Rang: Level 4.", messages[1]["content"])
        self.assertIn("[Aktuelle Nachricht]", messages[1]["content"])


if __name__ == "__main__":
    unittest.main()
