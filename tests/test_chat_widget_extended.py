from __future__ import annotations

from types import SimpleNamespace

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, OptionList, RichLog

from src.widgets.chat import ChatView, _md_line
from src.widgets.chat import _parse_flashcards


class ChatHarness(App):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []
        self.quiz_events = []
        self.quiz_answer_events = []
        self.flashcard_review_events = []
        self.flashcard_review_finished_events = []

    def compose(self) -> ComposeResult:
        yield ChatView(id='chat')

    def on_chat_view_user_message(self, message: ChatView.UserMessage) -> None:
        self.messages.append(message.text)

    def on_chat_view_quiz_finished(self, event: ChatView.QuizFinished) -> None:
        self.quiz_events.append(event)

    def on_chat_view_quiz_answer_submitted(self, event: ChatView.QuizAnswerSubmitted) -> None:
        self.quiz_answer_events.append(event)

    def on_chat_view_flashcard_reviewed(self, event: ChatView.FlashcardReviewed) -> None:
        self.flashcard_review_events.append(event)

    def on_chat_view_flashcard_review_finished(self, event: ChatView.FlashcardReviewFinished) -> None:
        self.flashcard_review_finished_events.append(event)


def rendered_log(chat: ChatView) -> str:
    log = chat.query_one('#chat-log', RichLog)
    return '\n'.join(str(line) for line in log.lines)



@pytest.mark.asyncio
async def test_message_thinking_and_streaming_rendering() -> None:
    app = ChatHarness()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        inp = chat.query_one('#chat-input', Input)

        assert _md_line('**bold**').plain.strip().endswith('bold')

        chat.add_user_message('hello')
        chat.add_system_message('system note')
        chat.add_tool_start('searching')
        chat.add_tool_done('done')
        chat.add_error('oops')
        chat.add_info_block('Title', ['one', 'two'])

        chat.start_thinking()
        chat.stream_thinking_token('first line\nsecond line')
        chat.end_thinking()

        chat.show_typing()
        await pilot.pause()
        assert inp.disabled is True
        assert 'is thinking' in inp.placeholder
        chat._animate_typing()
        chat.hide_typing()
        assert inp.disabled is False

        chat.start_response()
        chat.stream_token('alpha\nbeta')
        final = chat.end_response()
        assert final == 'alpha\nbeta'
        assert chat._last_response == 'alpha\nbeta'

        chat.add_assistant_message('assistant block')
        await pilot.pause()
        rendered = rendered_log(chat)
        assert 'alpha' in rendered
        assert 'hello' in rendered
        assert 'system note' in rendered
        assert 'searching' in rendered
        assert 'done' in rendered
        assert 'oops' in rendered
        assert 'assistant block' in rendered
        assert 'done thinking' in rendered


@pytest.mark.asyncio
async def test_quiz_flow_and_finish_event() -> None:
    app = ChatHarness()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        inp = chat.query_one('#chat-input', Input)
        questions = [
            {
                'type': 'mcq',
                'question': 'Capital of France?',
                'options': ['a) Paris', 'b) Rome'],
                'answer': 'a',
                'explanation': 'Paris is the capital.',
            },
            {
                'type': 'short',
                'question': 'What does DNA stand for?',
                'answer': 'deoxyribonucleic acid',
                'explanation': 'That is the expansion.',
            },
        ]

        chat.start_quiz(questions)
        await pilot.pause()
        assert chat.quiz_active is True
        assert inp.placeholder == 'Your answer...'

        chat._handle_quiz_input('a')
        chat._handle_quiz_input('')
        chat._handle_quiz_input('deoxyribonucleic acid')
        await pilot.pause()
        chat._handle_quiz_input('')
        await pilot.pause()

        assert chat.quiz_active is False
        assert app.quiz_events
        event = app.quiz_events[-1]
        assert event.score == 2
        assert event.total == 2
        assert inp.placeholder == 'Ask anything...    /help for commands'
        rendered = rendered_log(chat)
        assert 'RESULTS' in rendered
        assert 'outstanding' in rendered


@pytest.mark.asyncio
async def test_quiz_quit_and_fuzzy_match() -> None:
    app = ChatHarness()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        chat.start_quiz([
            {'type': 'short', 'question': 'Name a gas giant', 'answer': 'jupiter saturn', 'explanation': 'Either is okay.'}
        ])
        assert chat._fuzzy_match('jupiter', 'jupiter saturn') is True
        assert chat._fuzzy_match('deoxyribonucleic acd', 'deoxyribonucleic acid') is True
        assert chat._fuzzy_match('mars', 'jupiter saturn') is False
        chat._handle_quiz_input('/quit')
        await pilot.pause()
        assert chat.quiz_active is False
        assert app.quiz_events[-1].total == 0 or app.quiz_events[-1].total == 1


@pytest.mark.asyncio
async def test_numeric_quiz_answer_requests_async_verification() -> None:
    app = ChatHarness()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        inp = chat.query_one('#chat-input', Input)
        chat.start_quiz([
            {
                'type': 'numeric',
                'question': 'What is 6 x 7?',
                'answer': '42',
                'explanation': 'Six multiplied by seven equals forty-two.',
            }
        ])
        chat._handle_quiz_input('42')
        await pilot.pause()
        assert app.quiz_answer_events
        event = app.quiz_answer_events[-1]
        assert event.user_answer == '42'
        assert inp.disabled is True

        chat.complete_pending_numeric_answer(0, True, 'Equivalent value.')
        await pilot.pause()
        assert inp.disabled is False
        assert chat._quiz_answered is True


@pytest.mark.asyncio
async def test_autocomplete_completion_picker_and_key_navigation() -> None:
    app = ChatHarness()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        inp = chat.query_one('#chat-input', Input)
        options = chat.query_one('#cmd-suggest', OptionList)

        inp.value = '/su'
        await pilot.pause()
        assert options.display is True
        chat._complete_suggestion()
        assert inp.value == '/summary '

        inp.value = '/provider'
        await pilot.pause()
        assert options.display is True
        assert app.messages == []
        chat._complete_suggestion()
        await pilot.pause()
        assert app.messages[-1] == '/provider'

        chat.show_nested_picker('Choose provider', [('provider:openai', ' openai', '/provider openai')])
        await pilot.pause()
        assert options.display is True
        assert chat._option_mode == 'picker'
        chat._submit_selected_option()
        await pilot.pause()
        assert app.messages[-1] == '/provider openai'

        chat.show_nested_picker('Choose provider', [('provider:openai', ' openai', '/provider openai')])
        await pilot.pause()
        options.highlighted = 0
        chat.on_key(type('Evt', (), {'key': 'tab', 'prevent_default': lambda self: None, 'stop': lambda self: None})())
        await pilot.pause()
        assert app.messages[-1] == '/provider openai'

        inp.value = '/theme'
        await pilot.pause()
        chat.on_key(type('Evt', (), {'key': 'tab', 'prevent_default': lambda self: None, 'stop': lambda self: None})())
        await pilot.pause()
        assert app.messages[-1] == '/theme'

        chat.show_nested_picker(
            'Resolve this write request.',
            [
                ('approval:approve', ' approve Continue with the write', '/approve'),
                ('approval:deny', ' deny Cancel the write', '/deny'),
            ],
        )
        await pilot.pause()
        options.highlighted = 1
        chat.on_key(type('Evt', (), {'key': 'tab', 'prevent_default': lambda self: None, 'stop': lambda self: None})())
        await pilot.pause()
        assert app.messages[-1] == '/deny'

        inp.value = '/'
        await pilot.pause()
        start = options.highlighted
        chat.on_key(type('Evt', (), {'key': 'down', 'prevent_default': lambda self: None, 'stop': lambda self: None})())
        assert options.highlighted == min(start + 1, options.option_count - 1)
        chat.on_key(type('Evt', (), {'key': 'up', 'prevent_default': lambda self: None, 'stop': lambda self: None})())
        assert options.highlighted == start
        chat.on_key(type('Evt', (), {'key': 'escape', 'prevent_default': lambda self: None, 'stop': lambda self: None})())
        assert options.display is False


@pytest.mark.asyncio
async def test_option_click_submission_and_clear_focus_helpers() -> None:
    app = ChatHarness()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        inp = chat.query_one('#chat-input', Input)
        options = chat.query_one('#cmd-suggest', OptionList)

        inp.value = '/fl'
        await pilot.pause()
        first = options.get_option_at_index(0)
        chat.on_option_list_option_selected(type('Evt', (), {'option_list': options, 'option': first})())
        assert inp.value.endswith(' ')

        chat.show_nested_picker('Choose provider', [('provider:openai', ' openai', '/provider openai')])
        await pilot.pause()
        first = options.get_option_at_index(0)
        chat.on_option_list_option_selected(type('Evt', (), {'option_list': options, 'option': first})())
        await pilot.pause()
        assert options.display is False
        assert inp.has_focus is True

        chat.clear_log()
        chat.focus_input()
        await pilot.pause()
        assert inp.has_focus is True


@pytest.mark.asyncio
async def test_flashcards_render_as_cards() -> None:
    app = ChatHarness()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        chat.add_assistant_message(
            "Here are flashcards covering the main concepts:\n\n"
            "1. Q: What is chemistry?\n"
            "A: Chemistry is the branch of science that studies matter.\n\n"
            "2. Q: What is matter?\n"
            "A: Matter is anything that has mass and occupies space."
        )
        await pilot.pause()
        rendered = rendered_log(chat)
        assert 'Card 1' in rendered
        assert 'Card 2' in rendered
        assert 'What is chemistry?' in rendered
        assert 'answer hidden' in rendered
        assert 'anything that has mass and occupies space' not in rendered


@pytest.mark.asyncio
async def test_flashcards_render_numbered_question_answer_pairs() -> None:
    app = ChatHarness()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        chat.add_assistant_message(
            "Here are flashcards covering the main concepts from the loaded document:\n\n"
            "1. What is meant by a physical quantity in physics?\n"
            "A physical quantity is any measurable property that can be expressed with a numerical value and a unit.\n\n"
            "2. Why is physics called a quantitative science?\n"
            "Physics is called a quantitative science because it is based on measurement of physical quantities."
        )
        await pilot.pause()
        rendered = rendered_log(chat)
        assert 'Card 1' in rendered
        assert 'Card 2' in rendered
        assert 'What is meant by a physical quantity in physics?' in rendered
        assert 'answer hidden' in rendered
        assert 'measurement of physical quantities' not in rendered


@pytest.mark.asyncio
async def test_flashcards_render_parenthesized_numbered_pairs() -> None:
    app = ChatHarness()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        chat.add_assistant_message(
            "Flashcards from the chapter:\n\n"
            "1) Q: What is heat?\n"
            "A: Heat is energy transferred because of a temperature difference.\n\n"
            "2) Q: What is thermal equilibrium?\n"
            "A: It is the state where no net heat flows between bodies."
        )
        await pilot.pause()
        rendered = rendered_log(chat)
        assert 'Card 1' in rendered
        assert 'What is heat?' in rendered
        assert 'answer hidden' in rendered
        assert 'temperature difference' not in rendered


@pytest.mark.asyncio
async def test_flashcards_render_bulleted_qa_with_intro_and_outro() -> None:
    app = ChatHarness()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        chat.add_assistant_message(
            "Yes, the chapter is loaded.\n\n"
            "Here are study flashcards:\n\n"
            "▸ Q: What is heat?\n"
            "A: Heat is energy transferred due to a temperature difference.\n\n"
            "▸ Q: What is thermal equilibrium?\n"
            "A: It is the state where no net heat flows.\n\n"
            "If you want, I can make a harder set next."
        )
        await pilot.pause()
        rendered = rendered_log(chat)
        assert 'Yes, the chapter is loaded.' in rendered
        assert 'Card 1' in rendered
        assert 'What is heat?' in rendered
        assert 'answer hidden' in rendered
        assert 'temperature difference' not in rendered
        assert 'If you want, I can make a harder set next.' in rendered


@pytest.mark.asyncio
async def test_flashcard_review_reveals_then_advances() -> None:
    app = ChatHarness()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        inp = chat.query_one('#chat-input', Input)
        chat.start_flashcards(
            [
                {'question': 'What is measurement?', 'answer': 'Comparison with a standard.'},
                {'question': 'What is a unit?', 'answer': 'An accepted reference standard.'},
            ],
            intro_lines=['Here are flashcards for Units and Measurement.'],
        )
        await pilot.pause()
        rendered = rendered_log(chat)
        assert 'What is measurement?' in rendered
        assert 'Comparison with a standard.' not in rendered
        assert chat.flashcards_active is True
        assert inp.placeholder == 'enter ↵ to reveal'

        chat._handle_flashcard_input('')
        await pilot.pause()
        rendered = rendered_log(chat)
        assert 'Comparison with a standard.' in rendered
        assert inp.placeholder == 'enter ↵ for next card'

        chat._handle_flashcard_input('')
        await pilot.pause()
        rendered = rendered_log(chat)
        assert 'What is a unit?' in rendered

        chat._handle_flashcard_input('/quit')
        await pilot.pause()


@pytest.mark.asyncio
async def test_flashcard_review_shows_outro_on_finish() -> None:
    app = ChatHarness()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        chat.start_flashcards(
            [{'question': 'What is heat?', 'answer': 'Energy transfer due to temperature difference.'}],
            intro_lines=['Here are flashcards.'],
            outro_lines=['If you want, I can export these cards next.'],
        )
        chat._handle_flashcard_input('')
        chat._handle_flashcard_input('')
        await pilot.pause()
        rendered = rendered_log(chat)
        assert 'If you want, I can export these cards next.' in rendered


def test_parse_flashcards_prefers_explicit_flashcard_block() -> None:
    parsed = _parse_flashcards(
        "Here are the cards.\n"
        "[FLASHCARDS]\n"
        "Q: What is heat?\n"
        "A: Energy transfer due to temperature difference.\n\n"
        "Q: What is temperature?\n"
        "A: Degree of hotness.\n"
        "[/FLASHCARDS]\n"
        "Want me to export these next?"
    )
    assert parsed is not None
    intro, cards, outro = parsed
    assert intro == ["Here are the cards."]
    assert len(cards) == 2
    assert cards[0][0] == "What is heat?"
    assert cards[0][1] == "Energy transfer due to temperature difference."
    assert outro == ["Want me to export these next?"]

@pytest.mark.asyncio
async def test_flashcard_enter_submit_advances_review_mode() -> None:
    app = ChatHarness()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        inp = chat.query_one('#chat-input', Input)
        chat.start_flashcards(
            [
                {'question': 'What is measurement?', 'answer': 'Comparison with a standard.'},
                {'question': 'What is a unit?', 'answer': 'An accepted reference standard.'},
            ]
        )
        chat.on_input_submitted(SimpleNamespace(input=inp, value='', prevent_default=lambda: None))
        await pilot.pause()
        rendered = rendered_log(chat)
        assert 'Comparison with a standard.' in rendered
        assert inp.placeholder == 'enter ↵ for next card'

        chat.on_input_submitted(SimpleNamespace(input=inp, value='', prevent_default=lambda: None))
        await pilot.pause()
        rendered = rendered_log(chat)
        assert 'What is a unit?' in rendered
        assert 'An accepted reference standard.' not in rendered
        assert chat.flashcards_active is True
        assert inp.placeholder == 'enter ↵ to reveal'


@pytest.mark.asyncio
async def test_persistent_flashcard_review_uses_grades_and_emits_events() -> None:
    app = ChatHarness()
    async with app.run_test() as pilot:
        chat = app.query_one(ChatView)
        inp = chat.query_one('#chat-input', Input)
        chat.start_flashcards(
            [
                {'card_key': 'card-1', 'question': 'What is measurement?', 'answer': 'Comparison with a standard.'},
                {'card_key': 'card-2', 'question': 'What is a unit?', 'answer': 'An accepted reference standard.'},
            ],
            review_mode=True,
        )
        await pilot.pause()
        assert inp.placeholder == 'enter ↵ to reveal'

        chat._handle_flashcard_input('')
        await pilot.pause()
        assert inp.placeholder == '1 again · 2 hard · enter/3 good · 4 easy'

        chat._handle_flashcard_input('2')
        await pilot.pause()
        assert app.flashcard_review_events[-1].grade == 'hard'
        assert app.flashcard_review_events[-1].card['card_key'] == 'card-1'

        chat._handle_flashcard_input('')
        await pilot.pause()
        chat._handle_flashcard_input('')
        await pilot.pause()
        chat._handle_flashcard_input('')
        await pilot.pause()
        assert app.flashcard_review_events[-1].grade == 'good'
        assert app.flashcard_review_finished_events
        finished = app.flashcard_review_finished_events[-1]
        assert finished.total == 2
        assert finished.grades['hard'] == 1
        assert finished.grades['good'] == 1

