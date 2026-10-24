"""Supported native handler integration; never starts a Telegram consumer."""
from datetime import timedelta
import logging
import sqlite3
from pathlib import Path
from hermes_constants import get_hermes_home
from .config import ConfigError, load_config
from .store import Event, Store

logger = logging.getLogger(__name__)


def register(ctx):
    ctx.register_telegram_handler(wire_static_live_v2)


def wire_static_live_v2(native, adapter):
    # Stable versioned identity is required for the supported native rewire API:
    # (plugin name, factory qualname) is deduplicated for a connected application.
    # Preempt the v0.1 group -10 handler without removing/patching that handler.
    # Called inside the owning runtime scope. Keep that home when callbacks later
    # execute on another task/context. The adapter is read-only and not patched.
    from telegram.ext import ApplicationHandlerStop, MessageHandler, filters
    home = Path(get_hermes_home()).resolve()
    try:
        config = load_config(home)
    except (ConfigError, OSError):
        logger.warning('Live location persistence disabled: invalid or unreadable settings')
        config = None
    # Loading the plugin is the privacy boundary: even unconfigured/disabled
    # persistence must not send an incoming live stream to the ordinary LLM pin path.
    store = (Store(home, config.bot_id, config.owner_user_id, config.stale_after_seconds)
             if config is not None and config.enabled else None)

    class LocationUpdates(filters.UpdateFilter):
        def filter(self, update):
            # Inspect the update kind, not optional edit_date. Unknown/inactive
            # streams must not become ordinary LLM pins even without that field.
            message = update.effective_message
            return bool(message and (message.location or message.venue))

    async def ingest(update, context):
        message = update.effective_message
        location = message.venue.location if message.venue else message.location
        edited = update.edited_message is not None or update.edited_channel_post is not None
        quiet = edited or location.live_period is not None
        try:
            if store is None or config is None:
                return
            if (context.bot.id != config.bot_id or message is None
                    or message.chat.type != 'private' or message.chat.id != config.owner_user_id
                    or message.from_user is None or message.from_user.id != config.owner_user_id
                    or message.from_user.is_bot or message.sender_chat is not None
                    or message.forward_origin is not None
                    or message.api_kwargs.get('forward_from') is not None
                    or message.api_kwargs.get('forward_date') is not None
                    or message.is_automatic_forward):
                return
            period = location.live_period
            if isinstance(period, timedelta):
                seconds = period.total_seconds()
                period = int(seconds) if seconds.is_integer() else -1
            timestamp = message.edit_date or message.date
            store.ingest(Event(
                update_id=update.update_id, message_id=message.message_id,
                bot_id=context.bot.id, owner_id=message.from_user.id,
                original_at=message.date.timestamp(), observed_at=timestamp.timestamp(),
                latitude=location.latitude, longitude=location.longitude,
                live_period=period, edited=edited,
                accuracy=location.horizontal_accuracy,
            ))
        except (OSError, ValueError, sqlite3.Error):
            # No exception payloads/coordinates/identities in logs, and never fall
            # through for live/edited updates if local persistence is unavailable.
            logger.warning('Location persistence unavailable: local state unavailable')
        finally:
            # Live/edits never reach core, even on rejection/failure. New static
            # pins retain the original core semantics regardless of persistence.
            if quiet:
                raise ApplicationHandlerStop

    native.add_handler(MessageHandler(LocationUpdates(), ingest, block=True), group=-11)
