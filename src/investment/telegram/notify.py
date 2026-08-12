"""Send a message to the owner (docs/TASKS.md Phase 6bis; CLAUDE.md
"Scheduling": the Monday chain is "abort + Telegram alert on failure").

THE SEND HALF ONLY. `telegram/bot.py` (Task 6bis.2, UC9) is the receive half —
handlers, buttons, the chat — and it is a much bigger thing that pulls in
python-telegram-bot's `Application`, a running updater and a command layer. The
chain needs neither: it needs to say one sentence to one chat when it aborts,
and to deliver the Monday digest. Splitting the two lets `main.py` depend on the
sentence without depending on the bot.

FAILING TO NOTIFY MUST NOT FAIL THE CALLER, and this is the whole design
constraint. The alert exists because the chain broke; a raise here would replace
a reported failure with an unreported one — the worst possible trade. So `notify`
swallows and LOGS, and returns whether it got through. The one caller that wants
the hard version (a smoke test, on demand) calls `send_message` directly.

NO RETRY LOOP. python-telegram-bot's `Bot` already retries transport faults
internally; what is left after that is a wrong token, a wrong chat id, or no
network — none of which a second attempt fixes, and all of which the log line
names precisely.
"""

import logging

from telegram import Bot
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

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


async def notify(token: str, chat_id: str, text: str) -> bool:
    """`send_message`, but a failure is logged and reported, never raised.

    Returns True if the message got through. Every scheduled caller uses this
    one: notification is the LAST step of whatever it reports on, and taking the
    caller down with it turns a described failure into a silent one."""
    try:
        await send_message(token, chat_id, text)
    except TelegramError as exc:
        # Not `except Exception`: a TelegramError is "the message did not get
        # through", which is what this function is allowed to absorb. Anything
        # else (a bad type, a broken event loop) is a bug here and must surface.
        logger.error("telegram notify failed (%s): %s", type(exc).__name__, exc)
        return False
    return True
