import os
import threading
from flask import Flask
import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
from typing import Optional
import json
import aiohttp

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
MEME_CHANNEL_ID = int(os.getenv("MEME_CHANNEL_ID"))  # Neu für Reddit Memes

if TEMP_VC_CATEGORY_ID == 0 or CREATE_VC_CHANNEL_ID == 0:
    print("⚠️ Bitte TEMP_VC_CATEGORY_ID und CREATE_VC_CHANNEL_ID als Umgebungsvariablen setzen!")

# Reddit API Credentials aus Umgebungsvariablen
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "discord-bot-meme-fetcher")

# Mapping: User ID -> Temp Voice Channel ID
temp_voice_channels = {}

# Reaction Roles laden / speichern
REACTION_ROLE_FILE = "reaction_roles.json"

try:
    with open(REACTION_ROLE_FILE, "r") as f:
        reaction_roles = json.load(f)
except FileNotFoundError:
    reaction_roles = {}

def save_reaction_roles():
    with open(REACTION_ROLE_FILE, "w") as f:
        json.dump(reaction_roles, f, indent=4)

# --- On Ready ---
@bot.event
async def on_ready():
    print(f"✅ Bot ist online: {bot.user}")
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"🔁 Slash Commands synchronisiert: {len(synced)}")
    except Exception as e:
        print(f"Fehler beim Synchronisieren: {e}")
    # Starte Reddit Meme Task
    fetch_reddit_memes.start()

# --- Voice State Update: Temp VC Logik ---
@bot.event
async def on_voice_state_update(member, before, after):
    global temp_voice_channels

    if member.bot:
        return  # Bots ignorieren

    guild = member.guild

    # Temp VC erstellen, wenn User dem "Create Voice" Channel beitritt
    if after.channel and after.channel.id == CREATE_VC_CHANNEL_ID:
        category = guild.get_channel(TEMP_VC_CATEGORY_ID)
        if category is None:
            print("⚠️ Kategorie für Temp-VC nicht gefunden!")
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=False),
            member: discord.PermissionOverwrite(manage_channels=True, connect=True, speak=True, view_channel=True)
        }
        new_vc = await guild.create_voice_channel(
            name=f"Voicechat von {member.display_name}",
            category=category,
            overwrites=overwrites
        )
        temp_voice_channels[member.id] = new_vc.id
        await member.move_to(new_vc)

    # Temp VC löschen, wenn dieser leer wird
    if before.channel and before.channel.id in temp_voice_channels.values():
        channel = before.channel
        if len(channel.members) == 0:
            await channel.delete()
            # Eintrag im Mapping entfernen
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

# --- Reaction Role Befehle ---

# /reactionrole_add
@bot.tree.command(name="reactionrole_add", description="Füge eine Reaction Role hinzu", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(message_id="ID der Nachricht", emoji="Emoji für die Rolle", role="Rolle, die vergeben werden soll")
async def reactionrole_add(interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("❌ Du hast keine Berechtigung, Reaction Roles zu verwalten.", ephemeral=True)
        return

    try:
        channel = interaction.channel
        message = await channel.fetch_message(int(message_id))
    except Exception:
        await interaction.response.send_message("❌ Nachricht nicht gefunden.", ephemeral=True)
        return

    key = f"{channel.id}-{message_id}"
    if key not in reaction_roles:
        reaction_roles[key] = {}

    reaction_roles[key][emoji] = role.id
    save_reaction_roles()

    # Reaction hinzufügen
    try:
        await message.add_reaction(emoji)
    except Exception:
        await interaction.response.send_message("❌ Emoji konnte nicht hinzugefügt werden.", ephemeral=True)
        return

    await interaction.response.send_message(f"✅ Reaction Role wurde hinzugefügt: {emoji} → {role.name}", ephemeral=True)

# /reactionrole_remove
@bot.tree.command(name="reactionrole_remove", description="Entferne eine Reaction Role", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(message_id="ID der Nachricht", emoji="Emoji der Reaction Role, die entfernt werden soll")
async def reactionrole_remove(interaction: discord.Interaction, message_id: str, emoji: str):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("❌ Du hast keine Berechtigung, Reaction Roles zu verwalten.", ephemeral=True)
        return

    channel = interaction.channel
    key = f"{channel.id}-{message_id}"
    if key not in reaction_roles or emoji not in reaction_roles[key]:
        await interaction.response.send_message("❌ Diese Reaction Role existiert nicht.", ephemeral=True)
        return

    try:
        message = await channel.fetch_message(int(message_id))
        await message.clear_reaction(emoji)
    except Exception:
        await interaction.response.send_message("❌ Nachricht oder Reaction nicht gefunden.", ephemeral=True)
        return

    del reaction_roles[key][emoji]
    if not reaction_roles[key]:
        del reaction_roles[key]

    save_reaction_roles()
    await interaction.response.send_message(f"✅ Reaction Role {emoji} wurde entfernt.", ephemeral=True)

# --- Reddit Meme Fetching ---

last_post_id = None  # um nur neue Posts zu posten

@tasks.loop(minutes=30)
async def fetch_reddit_memes():
    global last_post_id
    if not all([REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, MEME_CHANNEL_ID]):
        print("⚠️ Reddit API oder Meme Channel nicht richtig konfiguriert.")
        return

    url = "https://www.reddit.com/r/deutschememes/hot.json?limit=10"
    headers = {"User-Agent": REDDIT_USER_AGENT}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                print(f"Fehler beim Abrufen von Reddit: Status {resp.status}")
                return
            data = await resp.json()

    posts = data["data"]["children"]

    channel = bot.get_channel(MEME_CHANNEL_ID)
    if channel is None:
        print("⚠️ Meme Channel nicht gefunden!")
        return

    new_last_post_id = last_post_id

    for post in posts[::-1]:  # von alt nach neu prüfen
        p = post["data"]
        # Prüfe, ob Post ein Bild ist
        if p.get("post_hint") != "image":
            continue
        post_id = p["id"]
        if last_post_id == post_id:
            # Alle neuen Posts wurden bereits gepostet
            break

        title = p["title"]
        image_url = p["url"]

        try:
            await channel.send(f"**{title}**\n{image_url}")
        except Exception as e:
            print(f"Fehler beim Senden des Memes: {e}")

        new_last_post_id = post_id

    if new_last_post_id:
        last_post_id = new_last_post_id

# --- Bot Start ---
bot.run(os.getenv("DISCORD_TOKEN"))