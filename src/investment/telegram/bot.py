"""The Telegram front (docs/TASKS.md Task 6bis.2; docs/USE_CASES.md UC9).

A THIN FRONT over `ops/commands.py` (ADR-005): every handler here parses a
message, calls one command, and renders what comes back. No handler queries the
database, validates a number or decides anything — if a rule appears in this
file it is a rule that will disagree with the CLI and the dashboard within a
month.

WHO MAY TALK TO IT. Only `TELEGRAM_CHAT_ID`. A bot token is a bearer credential
and Telegram will deliver anyone's message to it, so an unfiltered handler is a
command layer exposed to whoever finds the bot — including `/drawdown`, which
moves a binding rule. Everything else is refused without a reply: answering
"unauthorized" confirms the bot exists.

NO ACCEPT/REJECT BUTTONS, though Task 6bis.2 describes them. ADR-006 removed the
user-validation gate: a proposal that passes its gates IS the paper-test, and
`user_response` is a column nothing writes (`ops/cli.py` records the same).
Buttons for it would ask the owner a question the system does not wait on —
`ops/commands.py` carries the full argument.

PLAIN TEXT IS A NOTE, and the conversational chat UC9 also describes is NOT
here. The disambiguation between "a thought to file" and "a question to answer"
is a real design choice with no obvious default, and the chat needs the Worker
in a conversation loop with its three tools and a chat skill. Until that lands,
every non-command message is filed as a note — the qualitative-event channel
that already works — and says so, which is the honest behaviour rather than a
guess at intent.
"""

import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from investment.ops import commands
from investment.runtime import AgentRuntime

logger = logging.getLogger(__name__)

HELP = """Commands:
/status — is it alive, and how current
/ranking — the latest ranked portfolios
/refresh — catch-up now (market data, regime, NAV)
/chain — run the whole weekly chain now
/cycle — one ad-hoc cognitive cycle (max 1/day)
/enable <strategy_id> · /disable <strategy_id>
/drawdown <pct> — your binding drawdown rule, e.g. -20

Anything else you send is filed as a note and ingested within ~5 minutes."""


def build_application(runtime: AgentRuntime) -> Application:  # type: ignore[type-arg]
    """The bot, wired to the runtime. Handlers are registered here so the
    dispatch table is readable in one place."""
    application = Application.builder().token(runtime.settings.telegram_bot_token).build()
    allowed = str(runtime.settings.telegram_chat_id)

    def authorized(update: Update) -> bool:
        return update.effective_chat is not None and str(update.effective_chat.id) == allowed

    async def reply(update: Update, result: commands.CommandResult) -> None:
        if update.message is not None:
            await update.message.reply_text(commands.describe_result(result))

    async def on_help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if authorized(update) and update.message is not None:
            await update.message.reply_text(HELP)

    async def on_status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if authorized(update):
            await reply(update, await commands.status(runtime))

    async def on_ranking(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if authorized(update):
            await reply(update, await commands.ranking(runtime))

    async def on_refresh(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if authorized(update):
            await reply(update, await commands.refresh(runtime))

    async def on_chain(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if authorized(update):
            await reply(update, await commands.run_chain(runtime))

    async def on_cycle(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if authorized(update):
            await reply(update, await commands.run_cycle(runtime))

    async def on_enable(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if authorized(update):
            await reply(update, await _toggle(runtime, context.args, enabled=True))

    async def on_disable(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if authorized(update):
            await reply(update, await _toggle(runtime, context.args, enabled=False))

    async def on_drawdown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not authorized(update):
            return
        pct = commands.parse_float(context.args[0]) if context.args else None
        if pct is None:
            await reply(update, commands.CommandResult.refused("Usage: /drawdown -20"))
            return
        await reply(update, await commands.set_max_drawdown(runtime, pct))

    async def on_text(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """Anything that is not a command. See the module docstring: filed as a
        note, deliberately, until the chat handler exists."""
        if not authorized(update) or update.message is None or not update.message.text:
            return
        await reply(update, await commands.save_note(runtime, update.message.text))

    for name, handler in (
        ("start", on_help),
        ("help", on_help),
        ("status", on_status),
        ("ranking", on_ranking),
        ("refresh", on_refresh),
        ("chain", on_chain),
        ("cycle", on_cycle),
        ("enable", on_enable),
        ("disable", on_disable),
        ("drawdown", on_drawdown),
    ):
        application.add_handler(CommandHandler(name, handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return application


async def _toggle(
    runtime: AgentRuntime, args: list[str] | None, *, enabled: bool
) -> commands.CommandResult:
    """The two toggles differ by one boolean, so they share their parsing —
    including the missing-argument message, which is the half a user actually
    hits."""
    if not args:
        verb = "enable" if enabled else "disable"
        return commands.CommandResult.refused(f"Usage: /{verb} <strategy_id>")
    return await commands.set_strategy_enabled(runtime, args[0], enabled)
