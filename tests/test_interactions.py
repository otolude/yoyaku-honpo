import logging
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from discord_ai_reminder_bot.bot.interactions import (
    INTERNAL_ERROR_MESSAGE,
    PERMISSION_DENIED_MESSAGE,
    Phase1CommandTree,
    is_authorized_interaction,
    respond_ephemeral,
)

GUILD_ID = 100
ALLOWED_ROLE_ID = 200
SECRET_CONTENT = "private scheduled content"
SECRET_TOKEN = "private-bot-token"
SECRET_DATABASE_URL = "postgresql+psycopg://user:password@localhost/database"


def interaction(
    *,
    guild_id: int | None = GUILD_ID,
    include_guild: bool = True,
    member: bool = True,
    administrator: bool = False,
    role_ids: list[int] | None = None,
) -> MagicMock:
    value = MagicMock(spec=discord.Interaction)
    value.guild_id = guild_id
    value.guild = MagicMock(spec=discord.Guild) if include_guild else None
    if value.guild is not None:
        value.guild.id = guild_id
    if member:
        user = MagicMock(spec=discord.Member)
        user.guild = MagicMock(spec=discord.Guild)
        user.guild.id = guild_id
        permissions = MagicMock(spec=discord.Permissions)
        permissions.administrator = administrator
        user.guild_permissions = permissions
        roles = []
        for role_id in role_ids or []:
            role = MagicMock(spec=discord.Role)
            role.id = role_id
            role.guild = MagicMock(spec=discord.Guild)
            role.guild.id = guild_id
            roles.append(role)
        user.roles = roles
        value.user = user
    else:
        value.user = MagicMock(spec=discord.User)
    value.response = MagicMock(spec=discord.InteractionResponse)
    value.response.is_done.return_value = False
    value.response.send_message = AsyncMock()
    value.followup = MagicMock(spec=discord.Webhook)
    value.followup.send = AsyncMock()
    value.extras = {}
    return value


@pytest.mark.parametrize(
    "value",
    [
        interaction(administrator=True),
        interaction(role_ids=[ALLOWED_ROLE_ID]),
    ],
)
def test_administrator_or_allowed_role_is_authorized(value: MagicMock) -> None:
    assert is_authorized_interaction(
        value,
        configured_guild_id=GUILD_ID,
        allowed_role_ids=(ALLOWED_ROLE_ID,),
    )


@pytest.mark.parametrize(
    "value",
    [
        interaction(guild_id=None, include_guild=False, member=False),
        interaction(guild_id=999),
        interaction(include_guild=False),
        interaction(member=False),
        interaction(role_ids=[]),
        interaction(role_ids=[999]),
    ],
)
def test_unsafe_or_unauthorized_interaction_is_rejected(value: MagicMock) -> None:
    assert not is_authorized_interaction(
        value,
        configured_guild_id=GUILD_ID,
        allowed_role_ids=(ALLOWED_ROLE_ID,),
    )


@pytest.mark.parametrize("broken", ["member_guild", "permissions", "roles", "role_guild"])
def test_unverifiable_member_or_role_information_is_rejected(broken: str) -> None:
    value = interaction(role_ids=[ALLOWED_ROLE_ID])
    if broken == "member_guild":
        value.user.guild.id = 999
    elif broken == "permissions":
        value.user.guild_permissions = None
    elif broken == "roles":
        value.user.roles = None
    else:
        value.user.roles[0].guild = None
    assert not is_authorized_interaction(
        value,
        configured_guild_id=GUILD_ID,
        allowed_role_ids=(ALLOWED_ROLE_ID,),
    )


@pytest.mark.asyncio
async def test_initial_and_followup_responses_are_ephemeral_with_mentions_disabled() -> None:
    logger = logging.getLogger("test.interaction")
    initial = interaction()
    assert await respond_ephemeral(initial, "safe", logger=logger)
    initial.response.send_message.assert_awaited_once()
    _, kwargs = initial.response.send_message.await_args
    assert kwargs["ephemeral"] is True
    assert kwargs["allowed_mentions"].to_dict() == {"parse": []}


@pytest.mark.asyncio
async def test_embed_responses_support_initial_and_followup_without_content() -> None:
    logger = logging.getLogger("test.interaction")
    embed = discord.Embed(title="safe")
    initial = interaction()
    assert await respond_ephemeral(initial, embed=embed, logger=logger)
    assert initial.response.send_message.await_args.args == ()
    assert initial.response.send_message.await_args.kwargs["embed"] is embed
    assert initial.response.send_message.await_args.kwargs["ephemeral"] is True
    assert initial.response.send_message.await_args.kwargs["allowed_mentions"].to_dict() == {
        "parse": []
    }

    followup = interaction()
    followup.response.is_done.return_value = True
    assert await respond_ephemeral(followup, embed=embed, logger=logger)
    assert followup.followup.send.await_args.args == ()
    assert followup.followup.send.await_args.kwargs["embed"] is embed


@pytest.mark.asyncio
async def test_response_requires_exactly_one_of_content_or_embed() -> None:
    value = interaction()
    logger = logging.getLogger("test.interaction")
    with pytest.raises(ValueError):
        await respond_ephemeral(value, logger=logger)
    with pytest.raises(ValueError):
        await respond_ephemeral(value, "content", embed=discord.Embed(), logger=logger)

    followup = interaction()
    followup.response.is_done.return_value = True
    assert await respond_ephemeral(followup, "safe", logger=logger)
    followup.followup.send.assert_awaited_once()
    _, kwargs = followup.followup.send.await_args
    assert kwargs["ephemeral"] is True
    assert kwargs["allowed_mentions"].to_dict() == {"parse": []}


def tree_with_client() -> tuple[Phase1CommandTree, MagicMock]:
    client = MagicMock()
    client._connection._command_tree = None
    client.settings.discord_guild_id = GUILD_ID
    client.settings.discord_allowed_role_ids = (ALLOWED_ROLE_ID,)
    client.logger = logging.getLogger("test.command-tree")
    return Phase1CommandTree(client), client


@pytest.mark.asyncio
async def test_tree_denies_unauthorized_user_with_ephemeral_response() -> None:
    tree, _ = tree_with_client()
    value = interaction(role_ids=[])
    assert not await tree.interaction_check(value)
    value.response.send_message.assert_awaited_once()
    assert value.response.send_message.await_args.args == (PERMISSION_DENIED_MESSAGE,)
    assert value.response.send_message.await_args.kwargs["ephemeral"] is True
    assert value.response.send_message.await_args.kwargs["allowed_mentions"].to_dict() == {
        "parse": []
    }


@pytest.mark.asyncio
async def test_tree_error_boundary_returns_only_safe_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tree, _ = tree_with_client()
    value = interaction(role_ids=[ALLOWED_ROLE_ID])
    error = app_commands.CommandInvokeError(
        MagicMock(), RuntimeError(f"{SECRET_CONTENT} {SECRET_TOKEN} {SECRET_DATABASE_URL}")
    )
    with caplog.at_level(logging.ERROR):
        await tree.on_error(value, error)
    assert value.response.send_message.await_args.args == (INTERNAL_ERROR_MESSAGE,)
    assert SECRET_CONTENT not in caplog.text
    assert SECRET_TOKEN not in caplog.text
    assert SECRET_DATABASE_URL not in caplog.text


@pytest.mark.asyncio
async def test_authorization_error_boundary_does_not_send_twice() -> None:
    tree, _ = tree_with_client()
    value = interaction(role_ids=[])
    assert not await tree.interaction_check(value)
    await tree.on_error(value, app_commands.CheckFailure("denied"))
    value.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_response_failure_logs_only_fixed_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    value = interaction()
    value.response.send_message.side_effect = RuntimeError(
        f"{SECRET_CONTENT} {SECRET_TOKEN} {SECRET_DATABASE_URL}"
    )
    with caplog.at_level(logging.ERROR):
        assert not await respond_ephemeral(
            value,
            PERMISSION_DENIED_MESSAGE,
            logger=logging.getLogger("test.interaction"),
        )
    assert "interaction_response_failed" in caplog.text
    assert SECRET_CONTENT not in caplog.text
    assert SECRET_TOKEN not in caplog.text
    assert SECRET_DATABASE_URL not in caplog.text
