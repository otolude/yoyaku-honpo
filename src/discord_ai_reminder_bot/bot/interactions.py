"""Guild-scoped application-command authorization and safe responses."""

from __future__ import annotations

import logging
from collections.abc import Collection

import discord
from discord import app_commands
from discord.ext import commands

PERMISSION_DENIED_MESSAGE = "この操作を実行する権限がありません。"
INTERNAL_ERROR_MESSAGE = "処理中にエラーが発生しました。時間をおいて再度お試しください。"
_AUTHORIZATION_RESPONSE_KEY = "phase1_authorization_response_sent"


def is_authorized_interaction(
    interaction: discord.Interaction,
    *,
    configured_guild_id: int,
    allowed_role_ids: Collection[int],
) -> bool:
    """Authorize from the interaction payload without member-cache or REST lookups."""
    if interaction.guild_id != configured_guild_id:
        return False
    guild = interaction.guild
    if guild is None or guild.id != configured_guild_id:
        return False
    member = interaction.user
    if not isinstance(member, discord.Member) or member.guild.id != configured_guild_id:
        return False

    permissions = member.guild_permissions
    if permissions is None:
        return False
    if permissions.administrator is True:
        return True

    roles = member.roles
    if not isinstance(roles, list):
        return False
    observed_role_ids: set[int] = set()
    for role in roles:
        role_id = getattr(role, "id", None)
        role_guild = getattr(role, "guild", None)
        if not isinstance(role_id, int) or isinstance(role_id, bool):
            return False
        if role_guild is None or role_guild.id != configured_guild_id:
            return False
        observed_role_ids.add(role_id)
    return bool(observed_role_ids.intersection(allowed_role_ids))


async def respond_ephemeral(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
    logger: logging.Logger,
) -> bool:
    """Send one safe ephemeral response or followup without leaking failures."""
    if (content is None) == (embed is None):
        raise ValueError("exactly one of content or embed is required")
    mentions = discord.AllowedMentions.none()
    arguments = {"ephemeral": True, "allowed_mentions": mentions}
    if view is not None:
        arguments["view"] = view
    if embed is not None:
        arguments["embed"] = embed
    try:
        if interaction.response.is_done():
            if content is None:
                await interaction.followup.send(**arguments)
            else:
                await interaction.followup.send(content, **arguments)
        else:
            if content is None:
                await interaction.response.send_message(**arguments)
            else:
                await interaction.response.send_message(content, **arguments)
    except Exception:  # noqa: BLE001 - Discord errors can contain response bodies
        logger.error("interaction_response_failed")
        return False
    return True


class Phase1CommandTree(app_commands.CommandTree[commands.Bot]):
    """Apply Phase 1 guild authorization and a sanitized global error boundary."""

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        client = self.client
        settings = getattr(client, "settings", None)
        logger = getattr(client, "logger", logging.getLogger(__name__))
        if settings is None:
            await respond_ephemeral(interaction, PERMISSION_DENIED_MESSAGE, logger=logger)
            interaction.extras[_AUTHORIZATION_RESPONSE_KEY] = True
            return False
        authorized = is_authorized_interaction(
            interaction,
            configured_guild_id=settings.discord_guild_id,
            allowed_role_ids=settings.discord_allowed_role_ids,
        )
        if authorized:
            return True
        await respond_ephemeral(interaction, PERMISSION_DENIED_MESSAGE, logger=logger)
        interaction.extras[_AUTHORIZATION_RESPONSE_KEY] = True
        return False

    async def on_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError, /
    ) -> None:
        if interaction.extras.get(_AUTHORIZATION_RESPONSE_KEY):
            return
        logger = getattr(self.client, "logger", logging.getLogger(__name__))
        logger.error("application_command_failed")
        await respond_ephemeral(interaction, INTERNAL_ERROR_MESSAGE, logger=logger)
