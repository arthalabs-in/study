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
async def test_exact_picker_command_does_not_post_while_typing() -> None:
    app = HarnessApp()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        inp = chat.query_one('#chat-input', Input)
        inp.value = '/provider'
        await pilot.pause()
        options = chat.query_one('#cmd-suggest', OptionList)
        assert app.messages == []
        assert inp.value == '/provider'
        assert options.display is True
        assert options.get_option_at_index(0).id == '/provider'


@pytest.mark.asyncio
async def test_exact_picker_command_submits_on_enter() -> None:
    app = HarnessApp()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        inp = chat.query_one('#chat-input', Input)
        inp.value = '/provider'
        await pilot.pause()
        await pilot.press("enter")
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
async def test_nested_picker_filters_instead_of_hiding() -> None:
    app = HarnessApp()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        chat.show_nested_picker(
            'Choose a provider.',
            [
                ('provider:openai', ' openai OpenAI API [API]', '/provider openai'),
                ('provider:groq', ' groq Groq API [API]', '/provider groq'),
            ],
        )
        await pilot.pause()
        inp = chat.query_one('#chat-input', Input)
        options = chat.query_one('#cmd-suggest', OptionList)
        inp.value = 'groq'
        await pilot.pause()
        assert options.display is True
        assert options.option_count == 1
        assert options.get_option_at_index(0).id == 'provider:groq'


@pytest.mark.asyncio
async def test_welcome_screen_includes_resume_and_docdir() -> None:
    app = HarnessApp()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        log = chat.query_one('#chat-log', RichLog)
        chat.clear_log()
        chat.write_welcome(
            {
                "provider": "groq",
                "model": "llama-3.3-70b-versatile",
                "documents_dir": "C:/Users/Harsh/Documents",
                "recent_sessions": [{"title": "Thermo review", "messages": 8}],
                "loaded_documents": ["keph101.pdf"],
            }
        )
        await pilot.pause()
        rendered = '\n'.join(str(line) for line in log.lines)

        assert 'Study Workspace' in rendered
        assert 'groq' in rendered
        assert 'Core Workflows' in rendered
        assert '/resume' in rendered
        assert '/docdir' in rendered


@pytest.mark.asyncio
async def test_welcome_mascot_stays_visible_after_system_message() -> None:
    app = HarnessApp()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        log = chat.query_one('#chat-log', RichLog)
        chat.write_welcome({"provider": "groq"})
        await pilot.pause()
        before_lines = len(log.lines)
        chat.add_system_message("Connected to groq")
        await pilot.pause()
        assert len(log.lines) > before_lines


@pytest.mark.asyncio
async def test_welcome_layout_keeps_hero_and_workspace_in_compact_mode() -> None:
    app = HarnessApp()
    async with app.run_test(size=(92, 34)) as pilot:
        chat = app.query_one(ChatView)
        log = chat.query_one('#chat-log', RichLog)
        chat.write_welcome({"provider": "groq"})
        await pilot.pause()
        rendered = '\n'.join(str(line) for line in log.lines)
        assert "Study Workspace" in rendered
        assert "Core Workflows" in rendered
