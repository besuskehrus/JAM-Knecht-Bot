import os
import threading
from flask import Flask
import discord
from discord.ext import commands
from discord import app_commands
import asyncio

# --- Flask Webserver ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

threading.Thread(target=run_flask).start()

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
GUILD_ID = int(os.getenv("GUILD_ID"))

# Temporäre Voicechats nachverfolgen
TEMP_CATEGORY_ID = 1325517686259716138
JOIN_CHANNEL_NAME = "➕ Voicechat erstellen"
active_temp_vcs = {}  # {channel_id: user_id}

# --- Events ---
@bot.event
async def on_ready():
    print(f"✅ Bot ist online: {bot.user}")
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"🔁 Slash Commands synchronisiert: {len(synced)}")
    except Exception as e:
        print(f"❌ Fehler beim Synchronisieren: {e}")

@bot.event
async def on_voice_state_update(member, before, after):
    # --- Spotify Hinweis bei VC-Join ---
    if after.channel and (before.channel != after.channel) and not member.bot:
        channel_name = after.channel.name
        text_channel = discord.utils.get(member.guild.text_channels, name="jam-links")
        if text_channel:
            await text_channel.send(f"{member.mention} ist dem VC **{channel_name}** beigetreten 🎧\n"
                                    f"Bitte teile deinen Spotify Jam-Link mit `/jam`.")

    # --- Temporäre Voicechats erstellen ---
    if after.channel and after.channel.name == JOIN_CHANNEL_NAME:
        category = discord.utils.get(member.guild.categories, id=TEMP_CATEGORY_ID)
        if not category:
            print("❌ Kategorie nicht gefunden.")
            return

        new_channel = await member.guild.create_voice_channel(
            name=f"Voicechat von {member.display_name}",
            category=category,
            user_limit=0,
            overwrites={
                member.guild.default_role: discord.PermissionOverwrite(connect=True, view_channel=True),
                member: discord.PermissionOverwrite(manage_channels=True)
            }
        )
        active_temp_vcs[new_channel.id] = member.id
        await member.move_to(new_channel)

    # --- Temporäre Voicechats löschen ---
    if before.channel and before.channel.id in active_temp_vcs:
        if len(before.channel.members) == 0:
            await before.channel.delete()
            del active_temp_vcs[before.channel.id]

# --- Slash Command: /jam ---
@bot.tree.command(name="jam", description="Spotify Jam-Link posten", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(link="Dein Spotify Jam-Link")
async def jam(interaction: discord.Interaction, link: str):
    if "spotify.link" not in link:
        await interaction.response.send_message("❌ Das ist kein gültiger Spotify Jam-Link!", ephemeral=True)
        return

    view = discord.ui.View()
    button = discord.ui.Button(label="🎶 Jam beitreten", url=link)
    view.add_item(button)

    await interaction.response.send_message(
        f"{interaction.user.mention} hat einen Spotify Jam gestartet:",
        view=view
    )

# --- Slash Command: /verstecken ---
@bot.tree.command(name="verstecken", description="Blendet deinen Voicechat für andere aus", guild=discord.Object(id=GUILD_ID))
async def verstecken(interaction: discord.Interaction):
    user = interaction.user
    channel = user.voice.channel if user.voice else None

    if not channel or channel.id not in active_temp_vcs or active_temp_vcs[channel.id] != user.id:
        await interaction.response.send_message("❌ Du befindest dich nicht in deinem eigenen Voicechat.", ephemeral=True)
        return

    overwrite = channel.overwrites_for(channel.guild.default_role)
    overwrite.view_channel = False
    await channel.set_permissions(channel.guild.default_role, overwrite=overwrite)
    await interaction.response.send_message("🙈 Dein Voicechat ist jetzt versteckt.", ephemeral=True)

# --- Slash Command: /einladen ---
@bot.tree.command(name="einladen", description="Gib bestimmten Mitgliedern Zugriff auf deinen Voicechat", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(mitglieder="Mitglieder, die Zugriff bekommen sollen")
async def einladen(interaction: discord.Interaction, mitglieder: list[discord.Member]):
    user = interaction.user
    channel = user.voice.channel if user.voice else None

    if not channel or channel.id not in active_temp_vcs or active_temp_vcs[channel.id] != user.id:
        await interaction.response.send_message("❌ Du befindest dich nicht in deinem eigenen Voicechat.", ephemeral=True)
        return

    for member in mitglieder:
        await channel.set_permissions(member, view_channel=True, connect=True)

    namen = ", ".join([m.display_name for m in mitglieder])
    await interaction.response.send_message(f"📢 Zugriff gewährt für: {namen}", ephemeral=True)

# --- Slash Command: /limit ---
@bot.tree.command(name="limit", description="Setzt die maximale Teilnehmerzahl für deinen Voicechat", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(anzahl="Maximale Teilnehmerzahl (0 = unbegrenzt)")
async def limit(interaction: discord.Interaction, anzahl: int):
    initiator = interaction.user
    channel = initiator.voice.channel if initiator.voice else None

    if not channel or channel.id not in active_temp_vcs or active_temp_vcs[channel.id] != initiator.id:
        await interaction.response.send_message("❌ Du befindest dich nicht in deinem eigenen Voicechat.", ephemeral=True)
        return

    if anzahl < 0 or anzahl > 99:
        await interaction.response.send_message("⚠️ Bitte gib eine Zahl zwischen 0 und 99 an (0 = unbegrenzt).", ephemeral=True)
        return

    await channel.edit(user_limit=anzahl)
    if anzahl == 0:
        await interaction.response.send_message("🔓 Teilnehmerbegrenzung wurde entfernt.", ephemeral=True)
    else:
        await interaction.response.send_message(f"✅ Die Teilnehmerzahl wurde auf **{anzahl}** begrenzt.", ephemeral=True)

# --- Bot starten ---
bot.run(os.getenv("DISCORD_TOKEN"))