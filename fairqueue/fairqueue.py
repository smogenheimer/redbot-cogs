from __future__ import annotations

import time
from typing import Iterable, List, Optional

import discord
import lavalink
from lavalink import NodeNotFound
from redbot.core import commands


def _get_requester_id(track: lavalink.Track) -> Optional[int]:
    requester = getattr(track, "requester", None)
    if requester is not None:
        return getattr(requester, "id", None)
    return track.extras.get("requester") if hasattr(track, "extras") else None


def _fair_insert_index(queue: List[lavalink.Track], requester_id: int) -> int:
    last_index = -1
    for index in range(len(queue) - 1, -1, -1):
        if _get_requester_id(queue[index]) == requester_id:
            last_index = index
            break
    insert_at = last_index + 1
    seen_requesters = set()
    for index in range(insert_at, len(queue)):
        existing_id = _get_requester_id(queue[index])
        if existing_id is None:
            insert_at = index + 1
            continue
        if existing_id in seen_requesters:
            break
        seen_requesters.add(existing_id)
        insert_at = index + 1
    return insert_at


def _insert_tracks_fairly(
    queue: List[lavalink.Track], requester_id: int, tracks: Iterable[lavalink.Track]
) -> List[int]:
    indices = []
    for track in tracks:
        insert_at = _fair_insert_index(queue, requester_id)
        queue.insert(insert_at, track)
        indices.append(insert_at)
    return indices


class FairQueueCog(commands.Cog):
    """Queue audio fairly across users."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="p")
    @commands.guild_only()
    @commands.bot_has_permissions(embed_links=True)
    async def command_p(self, ctx: commands.Context, *, query: str) -> None:
        """Play the specified track with fair queue ordering."""
        audio = self.bot.get_cog("Audio")
        if audio is None:
            await ctx.send("The Audio cog is not loaded.")
            return

        from redbot.cogs.audio.audio_dataclasses import Query

        query_obj = Query.process_input(query, audio.local_folder_current_path)
        guild_data = await audio.config.guild(ctx.guild).all()
        restrict = await audio.config.restrict()
        if restrict and audio.match_url(str(query_obj)):
            valid_url = audio.is_url_allowed(str(query_obj))
            if not valid_url:
                return await audio.send_embed_msg(
                    ctx,
                    title="Unable To Play Tracks",
                    description=(
                        "That URL is not allowed.\n\n"
                        f"The bot owner can remove this restriction by using ``{ctx.clean_prefix}audioset restrict``."
                    ),
                )
        elif not await audio.is_query_allowed(audio.config, ctx, f"{query_obj}", query_obj=query_obj):
            return await audio.send_embed_msg(
                ctx, title="Unable To Play Tracks", description="That track is not allowed."
            )

        can_skip = await audio._can_instaskip(ctx, ctx.author)
        if guild_data["dj_enabled"] and not can_skip:
            return await audio.send_embed_msg(
                ctx,
                title="Unable To Play Tracks",
                description="You need the DJ role to queue tracks.",
            )

        if not audio._player_check(ctx):
            if audio.lavalink_connection_aborted:
                message = "Connection to Lavalink node has failed"
                description = None
                if await self.bot.is_owner(ctx.author):
                    description = "Please check your console or logs for details."
                return await audio.send_embed_msg(ctx, title=message, description=description)
            try:
                if (
                    not audio.can_join_and_speak(ctx.author.voice.channel)
                    or not ctx.author.voice.channel.permissions_for(ctx.me).move_members
                    and audio.is_vc_full(ctx.author.voice.channel)
                ):
                    return await audio.send_embed_msg(
                        ctx,
                        title="Unable To Play Tracks",
                        description="I don't have permission to connect and speak in your channel.",
                    )
                await lavalink.connect(
                    ctx.author.voice.channel,
                    self_deaf=await audio.config.guild_from_id(ctx.guild.id).auto_deafen(),
                )
            except AttributeError:
                return await audio.send_embed_msg(
                    ctx,
                    title="Unable To Play Tracks",
                    description="Connect to a voice channel first.",
                )
            except NodeNotFound:
                return await audio.send_embed_msg(
                    ctx,
                    title="Unable To Play Tracks",
                    description="Connection to the Lavalink node has not yet been established.",
                )

        player = lavalink.get_player(ctx.guild.id)
        player.store("notify_channel", ctx.channel.id)
        await audio._eq_check(ctx, player)
        await audio.set_player_settings(ctx)
        if (not ctx.author.voice or ctx.author.voice.channel != player.channel) and not can_skip:
            return await audio.send_embed_msg(
                ctx,
                title="Unable To Play Tracks",
                description="You must be in the voice channel to use the play command.",
            )
        if not query_obj.valid:
            return await audio.send_embed_msg(
                ctx,
                title="Unable To Play Tracks",
                description=f"No tracks found for `{query_obj.to_string_user()}`.",
            )
        if len(player.queue) >= 10000:
            return await audio.send_embed_msg(
                ctx, title="Unable To Play Tracks", description="Queue size limit reached."
            )
        if query_obj.is_spotify:
            return await audio.send_embed_msg(
                ctx,
                title="Unable To Play Tracks",
                description="Spotify queries are not supported for fair queueing.",
            )

        if not await audio.maybe_charge_requester(ctx, guild_data["jukebox_price"]):
            return

        try:
            tracks = await audio._enqueue_tracks(ctx, query_obj, enqueue=False)
        except Exception:
            audio.update_player_lock(ctx, False)
            raise

        if isinstance(tracks, discord.Message):
            return
        if not tracks:
            audio.update_player_lock(ctx, False)
            return await audio.send_embed_msg(
                ctx,
                title="Unable To Play Tracks",
                description=f"No tracks found for `{query_obj.to_string_user()}`.",
            )

        if isinstance(tracks, lavalink.Track):
            tracks_to_add = [tracks]
        else:
            tracks_to_add = list(tracks)

        for track in tracks_to_add:
            track.requester = ctx.author
            track.extras.update(
                {
                    "enqueue_time": int(time.time()),
                    "vc": player.channel.id,
                    "requester": ctx.author.id,
                }
            )

        indices = _insert_tracks_fairly(player.queue, ctx.author.id, tracks_to_add)
        player.maybe_shuffle()
        for track in tracks_to_add:
            self.bot.dispatch("red_audio_track_enqueue", player.guild, track, ctx.author)

        position_display = min(indices) + 1 if indices else len(player.queue)
        description = await audio.get_track_description(
            tracks_to_add[0], audio.local_folder_current_path
        )
        footer = None
        if not guild_data["shuffle"] and await audio.track_remaining_duration(ctx) > 0:
            footer = f"Queued at position #{position_display}."
        await audio.send_embed_msg(
            ctx, title="Track Enqueued", description=description, footer=footer
        )

        if not player.current:
            await player.play()

        audio.update_player_lock(ctx, False)
