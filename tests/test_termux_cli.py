import unittest
from types import SimpleNamespace
from unittest.mock import patch

import termux
from termux import is_exit_command


class ExitCommandTests(unittest.TestCase):
    def test_quit_command_is_case_insensitive(self):
        self.assertTrue(is_exit_command("/QUIT"))

    def test_exit_command_ignores_surrounding_whitespace(self):
        self.assertTrue(is_exit_command("  /exit  "))

    def test_regular_text_is_not_an_exit_command(self):
        self.assertFalse(is_exit_command("help me with Termux"))

    def test_chat_sends_text_and_prints_transcribed_reply(self):
        class FakeSession:
            def __init__(self):
                self.sent = []

            async def send_realtime_input(self, *, text):
                self.sent.append(text)

            async def receive(self):
                yield SimpleNamespace(
                    tool_call=None,
                    server_content=SimpleNamespace(
                        output_transcription=SimpleNamespace(text="Hello from JARVIS."),
                        turn_complete=False,
                    ),
                )
                yield SimpleNamespace(
                    tool_call=None,
                    server_content=SimpleNamespace(
                        output_transcription=None,
                        turn_complete=True,
                    ),
                )

        session = FakeSession()
        prompts = iter(["Hi", "/quit"])

        async def fake_to_thread(_func, *_args, **_kwargs):
            return next(prompts)

        printed = []
        with patch.object(termux.asyncio, "to_thread", fake_to_thread):
            with patch("builtins.print", lambda *args, **kwargs: printed.append(args[0] if args else "")):
                import asyncio

                asyncio.run(termux._chat_loop(session, "JARVIS"))

        self.assertEqual(session.sent, ["Hi"])
        self.assertIn("Hello from JARVIS.", printed)


if __name__ == "__main__":
    unittest.main()
