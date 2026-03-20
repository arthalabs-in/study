from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, OptionList, RichLog

from src.widgets.chat import ChatView


class HarnessApp(App):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def compose(self) -> ComposeResult:
        yield ChatView(id='chat')

    def on_chat_view_user_message(self, message: ChatView.UserMessage) -> None:
        self.messages.append(message.text)


@pytest.mark.asyncio
async def test_partial_command_shows_autocomplete() -> None:
    app = HarnessApp()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        inp = chat.query_one('#chat-input', Input)
        inp.value = '/pro'
        await pilot.pause()
        options = chat.query_one('#cmd-suggest', OptionList)
        assert options.display is True
        assert any(options.get_option_at_index(i).id == '/provider' for i in range(options.option_count))


@pytest.mark.asyncio
async def test_exact_picker_command_posts_message() -> None:
    app = HarnessApp()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        inp = chat.query_one('#chat-input', Input)
        inp.value = '/provider'
        await pilot.pause()
        assert app.messages == ['/provider']
        assert inp.value == ''


@pytest.mark.asyncio
async def test_show_nested_picker_sets_placeholder_and_options() -> None:
    app = HarnessApp()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        chat.show_nested_picker('Choose a provider.', [('provider:openai', ' openai OpenAI API [API]', '/provider openai')])
        await pilot.pause()
        inp = chat.query_one('#chat-input', Input)
        options = chat.query_one('#cmd-suggest', OptionList)
        assert inp.placeholder == 'Choose a provider.'
        assert options.display is True
        assert options.option_count == 1
        assert options.get_option_at_index(0).id == 'provider:openai'


@pytest.mark.asyncio
async def test_welcome_screen_includes_resume_and_docdir() -> None:
    app = HarnessApp()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        log = chat.query_one('#chat-log', RichLog)
        chat.clear_log()
        chat.write_welcome()
        await pilot.pause()
        rendered = '\n'.join(str(line) for line in log.lines)

        assert '/resume' in rendered
        assert '/docdir' in rendered
