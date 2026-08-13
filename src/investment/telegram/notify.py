"""Send a message to the owner (docs/TASKS.md Phase 6bis; CLAUDE.md
"Scheduling": the weekly chain is "abort + Telegram alert on failure").

THE SEND HALF ONLY. `telegram/bot.py` (Task 6bis.2, UC9) is the receive half —
handlers, buttons, the chat — and it is a much bigger thing that pulls in
python-telegram-bot's `Application`, a running updater and a command layer. The
chain needs neither: it needs to say one sentence to one chat when it aborts,
and to deliver the weekly digest. Splitting the two lets `main.py` depend on the
sentence without depending on the bot.

THE RAW TRANSPORT, and only that. `send_message` RAISES. Failing to notify must
not fail the caller — the alert exists because the chain broke, and a raise here
would replace a reported failure with an unreported one — but that rule is
`delivery.deliver`'s to enforce, because absorbing the error is only half of it:
the message still has to land somewhere, and `deliver` writes it to the outbox.
This module used to own a `notify()` that swallowed and returned a bool, which
lost the text; `deliver` replaced it and `notify()` sat unused (CLAUDE.md: "No
dead code, no speculative stubs").

NO RETRY LOOP HERE. python-telegram-bot's `Bot` retries transport faults inside
one call; what is left after that is a wrong token, a wrong chat id, or no
network — none of which a second immediate attempt fixes. Retrying ACROSS runs
would be a different feature with a persistent queue behind it (I-54).
"""

from telegram import Bot

# Telegram refuses a message body over 4096 characters, and the digest is the
# one message that can reach it (a long ranking, several alerts, a Worker
# reading). Truncated with a visible marker rather than split into a thread:
# the digest is read top-down and its head carries the alerts, so the tail is
# what can be lost — and a silent 400 from the API would lose ALL of it.
MAX_MESSAGE_CHARS = 4096
_TRUNCATION_MARK = "\n\n… (truncated — see `invest status`)"


def clip(text: str) -> str:
    """`text` within Telegram's limit, with the loss made visible."""
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    return text[: MAX_MESSAGE_CHARS - len(_TRUNCATION_MARK)] + _TRUNCATION_MARK


async def send_message(token: str, chat_id: str, text: str) -> None:
    """One message to the owner's chat. Raises `TelegramError` on failure.

    NO PARSE MODE — the argument is simply not passed, which is Telegram's
    plain-text default. The digest is full of characters Markdown would claim
    (`*`, `_`, `[`, and every `-25%` that would open an italic run), and a
    message that fails to PARSE is a message the owner never sees. Formatting is
    not worth that risk for a report read once a week."""
    await Bot(token).send_message(chat_id=chat_id, text=clip(text))
