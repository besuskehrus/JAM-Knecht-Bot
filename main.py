import os
import threading
from flask import Flask
import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import json
import aiohttp
from typing import Optional

# --- Flask Webserver ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# Starte Flask-Server in separatem Thread
threading.Thread(target=run_flask).start()

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Guild ID und wichtige Channel-IDs aus Umgebungsvariablen holen
GUILD_ID = int(os.getenv("GUILD_ID"))
TEMP_VC_CATEGORY_ID = int(os.getenv("TEMP_VC_CATEGORY_ID", 0))
CREATE_VC_CHANNEL_ID = int(os.getenv("CREATE_VC_CHANNEL_ID", 0))
MEME_CHANNEL_ID = int(os.getenv("MEME_CHANNEL_ID", 0))

if TEMP_VC_CATEGORY_ID == 0 or CREATE_VC_CHANNEL_ID == 0:
    print("⚠️ Bitte TEMP_VC_CATEGORY_ID und CREATE_VC_CHANNEL_ID als Umgebungsvariablen setzen!")

if MEME_CHANNEL_ID == 0:
    print("⚠️ Bitte MEME_CHANNEL_ID als Umgebungsvariable setzen!")

# Mapping: User ID -> Temp Voice Channel ID
temp_voice_channels = {}

# --- Reaction Roles Daten ---
REACTION_ROLES_FILE = "reaction_roles.json"
try:
    with open(REACTION_ROLES_FILE, "r", encoding="utf-8") as f:
        reaction_roles = json.load(f)
except FileNotFoundError:
    reaction_roles = {}

def save_reaction_roles():
    with open(REACTION_ROLES_FILE, "w", encoding="utf-8") as f:
        json.dump(reaction_roles, f, indent=4)

# --- Reddit Cache ---
LAST_SEEN_FILE = "last_seen_post.json"
try:
    with open(LAST_SEEN_FILE, "r", encoding="utf-8") as f:
        last_seen_post = json.load(f)
except FileNotFoundError:
    last_seen_post = {}

def save_last_seen_post():
    with open(LAST_SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(last_seen_post, f, indent=4)

# --- On Ready ---
@bot.event
async def on_ready():
    print(f"✅ Bot ist online: {bot.user}")
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"🔁 Slash Commands synchronisiert: {len(synced)}")
    except Exception as e:
        print(f"Fehler beim Synchronisieren: {e}")
    reddit_meme_poster.start()

# --- Voice State Update: Temp VC Logik ---
@bot.event
async def on_voice_state_update(member, before, after):
    global temp_voice_channels

    if member.bot:
        return

    guild = member.guild

    # Temp VC erstellen
    if after.channel and after.channel.id == CREATE_VC_CHANNEL_ID:
        category = guild.get_channel(TEMP_VC_CATEGORY_ID)
        if category is None:
            print("⚠️ Kategorie für Temp-VC nicht gefunden!")
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(connect=True, speak=True, view_channel=False),
            member: discord.PermissionOverwrite(manage_channels=True, connect=True, speak=True, view_channel=True)
        }
        new_vc = await guild.create_voice_channel(
            name=f"Voicechat von {member.display_name}",
            category=category,
            overwrites=overwrites
        )
        temp_voice_channels[member.id] = new_vc.id
        await member.move_to(new_vc)

    # Temp VC löschen wenn leer
    if before.channel and before.channel.id in temp_voice_channels.values():
        channel = before.channel
        if len(channel.members) == 0:
            await channel.delete()
            # Mapping bereinigen
            user_to_remove = None
            for uid, cid in temp_voice_channels.items():
                if cid == channel.id:
                    user_to_remove = uid
                    break
            if user_to_remove:
                del temp_voice_channels[user_to_remove]

# --- Slash Command: /verstecke ---
@bot.tree.command(name="verstecke", description="Verstecke deinen temporären Voicechat", guild=discord.Object(id=GUILD_ID))
async def verstecke(interaction: discord.Interaction):
    user_id = interaction.user.id
    if user_id not in temp_voice_channels:
        await interaction.response.send_message("❌ Du hast keinen temporären Voicechat.", ephemeral=True)
        return

    channel = bot.get_channel(temp_voice_channels[user_id])
    if channel is None:
        await interaction.response.send_message("❌ Dein Voicechat wurde nicht gefunden.", ephemeral=True)
        return

    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.view_channel = False
    await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)

    await interaction.response.send_message("✅ Dein Voicechat wurde versteckt.", ephemeral=True)

# --- Slash Command: /zeige ---
@bot.tree.command(name="zeige", description="Zeige deinen temporären Voicechat wieder an", guild=discord.Object(id=GUILD_ID))
async def zeige(interaction: discord.Interaction):
    user_id = interaction.user.id
    if user_id not in temp_voice_channels:
        await interaction.response.send_message("❌ Du hast keinen temporären Voicechat.", ephemeral=True)
        return

    channel = bot.get_channel(temp_voice_channels[user_id])
    if channel is None:
        await interaction.response.send_message("❌ Dein Voicechat wurde nicht gefunden.", ephemeral=True)
        return

    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.view_channel = True
    await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)

    await interaction.response.send_message("✅ Dein Voicechat ist jetzt wieder sichtbar.", ephemeral=True)

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

# --- Slash Command: /einladen ---
@bot.tree.command(
    name="einladen",
    description="Gib bestimmten Mitgliedern Zugriff auf deinen Voicechat",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    user1="Mitglied 1",
    user2="Mitglied 2",
    user3="Mitglied 3",
    user4="Mitglied 4",
    user5="Mitglied 5"
)
async def einladen(
    interaction: discord.Interaction,
    user1: discord.Member,
    user2: Optional[discord.Member] = None,
    user3: Optional[discord.Member] = None,
    user4: Optional[discord.Member] = None,
    user5: Optional[discord.Member] = None,
):
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("❌ Du bist in keinem Voice-Channel.", ephemeral=True)
        return

    channel = interaction.user.voice.channel
    users = [u for u in [user1, user2, user3, user4, user5] if u]

    for member in users:
        await channel.set_permissions(member, view_channel=True, connect=True, speak=True)

    mentions = ", ".join(m.mention for m in users)
    await interaction.response.send_message(
        f"✅ Folgende Mitglieder wurden eingeladen: {mentions}",
        ephemeral=True
    )

# --- Slash Command: /limit ---
@bot.tree.command(name="limit", description="Setze das maximale Teilnehmerlimit für deinen Voicechat", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(limit="Maximale Anzahl an Teilnehmern (0 für kein Limit)")
async def limit(interaction: discord.Interaction, limit: int):
    author = interaction.user
    category = interaction.guild.get_channel(TEMP_VC_CATEGORY_ID)

    if author.voice and author.voice.channel and author.voice.channel.category_id == category.id:
        voice_channel = author.voice.channel
    else:
        await interaction.response.send_message("❌ Du musst dich in deinem temporären Voicechat befinden.", ephemeral=True)
        return

    if limit < 0 or limit > 99:
        await interaction.response.send_message("❌ Bitte gib eine Zahl zwischen 0 und 99 ein.", ephemeral=True)
        return

    await voice_channel.edit(user_limit=limit if limit > 0 else 0)
    msg = "Teilnehmerlimit entfernt." if limit == 0 else f"Teilnehmerlimit auf {limit} gesetzt."
    await interaction.response.send_message(f"✅ {msg}", ephemeral=True)

# --- Slash Command: /reactionrole add ---
@bot.tree.command(name="
