import asyncio
import discord
from discord.ext import commands
import aiosqlite

DB_PATH = "database/bot.db"


class LimitModal(discord.ui.Modal):
    def __init__(self, channel: discord.VoiceChannel):
        super().__init__(title="인원 제한 변경")
        self.channel = channel

        self.limit = discord.ui.TextInput(
            label="인원 제한",
            placeholder="0은 제한 없음, 1~99 입력",
            required=True,
            max_length=2,
        )
        self.add_item(self.limit)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            limit = int(str(self.limit.value))
        except ValueError:
            await interaction.followup.send("❌ 숫자만 입력해주세요.", ephemeral=True)
            return

        if limit < 0 or limit > 99:
            await interaction.followup.send(
                "❌ 0~99 사이로 입력해주세요.", ephemeral=True
            )
            return

        try:
            await self.channel.edit(
                user_limit=limit,
                reason=f"{interaction.user} 님이 임시채널 인원 제한 변경",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ 봇에게 채널 관리 권한이 없습니다.", ephemeral=True
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                "❌ 인원 제한 변경 중 오류가 발생했습니다.", ephemeral=True
            )
            return

        await interaction.followup.send(
            f"✅ 인원 제한을 `{limit if limit else '제한 없음'}` 으로 변경했습니다.",
            ephemeral=True,
        )


class RenameModal(discord.ui.Modal):
    def __init__(self, channel_id: int):
        super().__init__(title="채널 이름 변경")
        self.channel_id = channel_id

        self.name_input = discord.ui.TextInput(
            label="새 채널 이름",
            placeholder="예: 롤 내전방",
            required=True,
            max_length=50,
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        new_name = str(self.name_input.value).strip()

        if not new_name:
            await interaction.followup.send("❌ 채널 이름을 입력해주세요.")
            return

        channel = interaction.guild.get_channel(self.channel_id)

        if not channel:
            await interaction.followup.send("❌ 채널을 찾을 수 없습니다.")
            return

        try:
            await channel.edit(
                name=new_name, reason=f"{interaction.user} 님이 임시채널 이름 변경"
            )
        except discord.Forbidden:
            await interaction.followup.send("❌ 봇에게 채널 관리 권한이 없습니다.")
            return
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"❌ 채널 이름 변경 중 오류가 발생했습니다: `{e}`"
            )
            return

        await interaction.followup.send(
            f"✅ 채널 이름을 `{new_name}` 으로 변경했습니다."
        )


class TempVoiceControlView(discord.ui.View):
    def __init__(self, channel_id: int):
        super().__init__(timeout=None)
        self.channel_id = channel_id

    async def is_owner(self, interaction: discord.Interaction) -> bool:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """
            SELECT owner_id
            FROM tempvoice_channels
            WHERE channel_id = ?
            """,
                (self.channel_id,),
            ) as cursor:
                row = await cursor.fetchone()

        if not row:
            await interaction.response.send_message(
                "❌ 이 임시채널 정보를 찾을 수 없습니다.", ephemeral=True
            )
            return False

        if interaction.user.id != row[0]:
            await interaction.response.send_message(
                "❌ 이 채널의 방장만 사용할 수 있습니다.", ephemeral=True
            )
            return False

        return True

    def get_channel(self, interaction: discord.Interaction):
        return interaction.guild.get_channel(self.channel_id)

    @discord.ui.button(
        label="🔒 잠금", style=discord.ButtonStyle.danger, custom_id="tempvoice_lock"
    )
    async def lock_channel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not await self.is_owner(interaction):
            return

        channel = self.get_channel(interaction)
        if not channel:
            await interaction.response.send_message(
                "❌ 채널을 찾을 수 없습니다.", ephemeral=True
            )
            return

        await channel.set_permissions(interaction.guild.default_role, connect=False)
        await interaction.response.send_message("🔒 채널을 잠갔습니다.", ephemeral=True)

    @discord.ui.button(
        label="🔓 해제", style=discord.ButtonStyle.success, custom_id="tempvoice_unlock"
    )
    async def unlock_channel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not await self.is_owner(interaction):
            return

        channel = self.get_channel(interaction)
        if not channel:
            await interaction.response.send_message(
                "❌ 채널을 찾을 수 없습니다.", ephemeral=True
            )
            return

        try:
            await channel.set_permissions(
                interaction.guild.default_role,
                connect=True,
                reason=f"{interaction.user} 님이 임시채널 잠금 해제",
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ 봇에게 채널 권한을 수정할 권한이 없습니다.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"❌ 잠금 해제 중 오류가 발생했습니다: `{e}`",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "🔓 채널 잠금을 해제했습니다.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="👥 인원변경",
        style=discord.ButtonStyle.primary,
        custom_id="tempvoice_limit",
    )
    async def change_limit(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not await self.is_owner(interaction):
            return

        channel = self.get_channel(interaction)
        if not channel:
            await interaction.response.send_message(
                "❌ 채널을 찾을 수 없습니다.", ephemeral=True
            )
            return

        await interaction.response.send_modal(LimitModal(channel))

    @discord.ui.button(
        label="✏️ 이름변경",
        style=discord.ButtonStyle.primary,
        custom_id="tempvoice_rename",
    )
    async def rename_channel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not await self.is_owner(interaction):
            return

        channel = self.get_channel(interaction)
        if not channel:
            await interaction.response.send_message(
                "❌ 채널을 찾을 수 없습니다.", ephemeral=True
            )
            return

        await interaction.response.send_modal(RenameModal(channel.id))


class TempVoiceCore(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        bot.add_view(TempVoiceControlView(0))

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot:
            return

        if before.channel == after.channel:
            return

        if before.channel:
            await self.handle_temp_channel_leave(before.channel, member)

        if after.channel is None:
            return

        creator_channel = after.channel

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """
            SELECT creator_channel_id
            FROM tempvoice_creators
            WHERE creator_channel_id = ?
            """,
                (creator_channel.id,),
            ) as cursor:
                data = await cursor.fetchone()

        if not data:
            return

        overwrites = dict(creator_channel.overwrites)

        new_channel = await member.guild.create_voice_channel(
            name=f"{member.display_name}의 영역",
            category=creator_channel.category,
            overwrites=overwrites,
        )

        await new_channel.set_permissions(
            member,
            manage_channels=True,
            move_members=True,
            mute_members=True,
            deafen_members=True,
        )

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
            INSERT OR REPLACE INTO tempvoice_channels (
                channel_id,
                owner_id
            )
            VALUES (?, ?)
            """,
                (new_channel.id, member.id),
            )
            await db.commit()

        try:
            await member.move_to(new_channel)
        except discord.HTTPException:
            await self.delete_temp_channel(new_channel)
            return

        await self.send_control_panel(new_channel, member)

    async def send_control_panel(
        self, channel: discord.VoiceChannel, owner: discord.Member
    ):
        embed = discord.Embed(
            title="🎛 채널 관리 패널",
            description=(
                f"👑 방장: {owner.mention}\n\n"
                "아래 버튼으로 채널을 관리할 수 있습니다."
            ),
            color=discord.Color.blurple(),
        )

        try:
            await channel.send(
                embed=embed,
                view=TempVoiceControlView(channel.id),
            )
        except discord.HTTPException:
            pass

    async def handle_temp_channel_leave(
        self,
        channel: discord.VoiceChannel,
        leaver: discord.Member,
    ):
        await asyncio.sleep(0.5)

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """
            SELECT owner_id
            FROM tempvoice_channels
            WHERE channel_id = ?
            """,
                (channel.id,),
            ) as cursor:
                data = await cursor.fetchone()

        if not data:
            return

        owner_id = data[0]

        if len(channel.members) == 0:
            await self.delete_temp_channel(channel)
            return

        if leaver.id == owner_id:
            new_owner = None

            for voice_member in channel.members:
                if not voice_member.bot:
                    new_owner = voice_member
                    break

            if new_owner:
                await self.transfer_owner(channel, leaver, new_owner)

    async def transfer_owner(
        self,
        channel: discord.VoiceChannel,
        old_owner: discord.Member,
        new_owner: discord.Member,
    ):
        try:
            await channel.set_permissions(old_owner, overwrite=None)
        except discord.HTTPException:
            pass

        await channel.set_permissions(
            new_owner,
            manage_channels=True,
            move_members=True,
            mute_members=True,
            deafen_members=True,
        )

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                             UPDATE tempvoice_channels
                             SET owner_id = ?
                             WHERE channel_id = ?
                             """,
                (new_owner.id, channel.id),
            )

            await db.commit()

        embed = discord.Embed(
            title="👑 방장 승계",
            description=f"새 방장: {new_owner.mention}",
            color=discord.Color.gold(),
        )

        try:
            await channel.send(embed=embed, view=TempVoiceControlView(channel.id))
        except discord.HTTPException:
            pass

    async def delete_temp_channel(self, channel: discord.VoiceChannel):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
            DELETE FROM tempvoice_channels
            WHERE channel_id = ?
            """,
                (channel.id,),
            )
            await db.commit()

        try:
            await channel.delete()
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(TempVoiceCore(bot))
