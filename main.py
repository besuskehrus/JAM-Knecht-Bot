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
from datetime import datetime

# --- Flask Webserver (uptime) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# Start Flask in eigenem Thread
threading.Thread(target=run_flask, daemon=True).start()

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- Konfiguration (aus Umgebungsvariablen) ---
GUILD_ID = int(os.getenv("GUILD_ID", 0))
TEMP_VC_CATEGORY_ID = int(os.getenv("TEMP_VC_CATEGORY_ID", 0))
CREATE_VC_CHANNEL_ID = int(os.getenv("CREATE_VC_CHANNEL_ID", 0))
MEME_CHANNEL_ID = int(os.getenv("MEME_CHANNEL_ID", 0))

REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "discord-bot-meme-fetcher")

# Warnung, falls wichtige IDs fehlen
if GUILD_ID == 0:
    print("⚠️ Bitte GUILD_ID als Umgebungsvariable setzen!")
if TEMP_VC_CATEGORY_ID == 0 or CREATE_VC_CHANNEL_ID == 0:
    print("⚠️ Bitte TEMP_VC_CATEGORY_ID und CREATE_VC_CHANNEL_ID als Umgebungsvariablen setzen!")
if MEME_CHANNEL_ID == 0:
    print("⚠️ Bitte MEME_CHANNEL_ID als Umgebungsvariable setzen!")

# --- Persistente Dateien ---
REACTION_ROLE_FILE = "reaction_roles.json"
LAST_SEEN_FILE = "last_seen_post.json"

# lade / initialisiere reaction_roles
try:
    with open(REACTION_ROLE_FILE, "r", encoding="utf-8") as f:
        reaction_roles = json.load(f)
except FileNotFoundError:
    reaction_roles = {}

def save_reaction_roles():
    with open(REACTION_ROLE_FILE, "w", encoding="utf-8") as f:
        json.dump(reaction_roles, f, indent=4, ensure_ascii=False)

# lade / initialisiere last_seen (speichert zuletzt gesehenen Post-ID)
try:
    with open(LAST_SEEN_FILE, "r", encoding="utf-8") as f:
        temp = json.load(f)
        last_seen_post = temp.get("last_seen")
except FileNotFoundError:
    last_seen_post = None

def save_last_seen(post_id: str):
    global last_seen_post
    last_seen_post = post_id
    with open(LAST_SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_seen": post_id}, f, indent=4)

# --- In-Memory Mapping für temporäre Voicechannels ---
temp_voice_channels: dict[int, int] = {}  # creator_user_id -> voice_channel_id

# --- Hilfsfunktion: Slash-Kommanden-Fehler für fehlende Rechte abfangen ---
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    from discord import app_commands as _ac
    if isinstance(error, _ac.MissingPermissions) or isinstance(error, _ac.CheckFailure):
        try:
            await interaction.response.send_message("❌ Du hast keine Berechtigung für diesen Befehl.", ephemeral=True)
        except Exception:
            pass
    else:
        # ungestörte Fehlerweitergabe (optional: loggen)
        print(f"[Command Error] {error}")

# --- On Ready: Slash-Commands sync + Start Reddit-Task ---
@bot.event
async def on_ready():
    print(f"✅ Bot ist online: {bot.user} (ID: {bot.user.id}) — {datetime.utcnow().isoformat()} UTC")
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"🔁 Slash Commands synchronisiert: {len(synced)}")
    except Exception as e:
        print(f"❌ Fehler beim Synchronisieren der Slash-Commands: {e}")

    # Starte den Reddit-Loop (falls nicht bereits gestartet)
    if not reddit_task.is_running():
        reddit_task.start()

# ---------------------------
# Temp Voice Channel Logik
# ---------------------------
@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    # Ignoriere Bots
    if member.bot:
        return

    # Erstelle Temp-VC wenn Nutzer in den "Create" Channel eintritt
    if after.channel and after.channel.id == CREATE_VC_CHANNEL_ID:
        guild = member.guild
        category = guild.get_channel(TEMP_VC_CATEGORY_ID)
        if category is None:
            print("⚠️ Kategorie für Temp-VC nicht gefunden!")
            return

        # WICHTIG: Wir setzen hier keine permissive @everyone-Overwrite,
        # damit die Kategorie-Rechte greifen; nur der Ersteller bekommt explizite Rechte.
        overwrites = {
            member: discord.PermissionOverwrite(
                manage_channels=True,
                connect=True,
                speak=True,
                view_channel=True
            )
        }

        new_vc = await guild.create_voice_channel(
            name=f"Voicechat von {member.display_name}",
            category=category,
            overwrites=overwrites
        )
        temp_voice_channels[member.id] = new_vc.id
        try:
            await member.move_to(new_vc)
        except Exception as e:
            print(f"⚠️ Fehler beim Moven des Nutzers: {e}")

    # Löschen des Temp-VC wenn leer
    if before.channel and before.channel.id in temp_voice_channels.values():
        channel = before.channel
        # channel könnte bereits None sein — prüfen
        if channel and len(channel.members) == 0:
            try:
                await channel.delete()
            except Exception as e:
                print(f"⚠️ Fehler beim Löschen des Channels: {e}")

            # Entferne Mapping-Eintrag
            remove_key = None
            for uid, cid in list(temp_voice_channels.items()):
                if cid == channel.id:
                    remove_key = uid
                    break
            if remove_key:
                del temp_voice_channels[remove_key]

# ---------------------------
# Slash Commands: verstecke / zeige / jam / einladen / limit
# ---------------------------
@bot.tree.command(name="verstecke", description="Verstecke deinen temporären Voicechat", guild=discord.Object(id=GUILD_ID))
async def verstecke(interaction: discord.Interaction):
    uid = interaction.user.id
    if uid not in temp_voice_channels:
        await interaction.response.send_message("❌ Du hast keinen temporären Voicechat.", ephemeral=True)
        return

    channel = bot.get_channel(temp_voice_channels[uid])
    if not channel:
        await interaction.response.send_message("❌ Dein Voicechat wurde nicht gefunden.", ephemeral=True)
        return

    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.view_channel = False
    await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await interaction.response.send_message("✅ Dein Voicechat wurde versteckt.", ephemeral=True)

@bot.tree.command(name="zeige", description="Zeige deinen temporären Voicechat", guild=discord.Object(id=GUILD_ID))
async def zeige(interaction: discord.Interaction):
    uid = interaction.user.id
    if uid not in temp_voice_channels:
        await interaction.response.send_message("❌ Du hast keinen temporären Voicechat.", ephemeral=True)
        return

    channel = bot.get_channel(temp_voice_channels[uid])
    if not channel:
        await interaction.response.send_message("❌ Dein Voicechat wurde nicht gefunden.", ephemeral=True)
        return

    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.view_channel = True
    await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await interaction.response.send_message("✅ Dein Voicechat ist jetzt sichtbar.", ephemeral=True)

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

@bot.tree.command(
    name="einladen",
    description="Gib bestimmten Mitgliedern Zugriff auf deinen Voicechat",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(user1="Mitglied 1", user2="Mitglied 2", user3="Mitglied 3", user4="Mitglied 4", user5="Mitglied 5")
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
    await interaction.response.send_message(f"✅ Folgende Mitglieder wurden eingeladen: {mentions}", ephemeral=True)

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

# ---------------------------
# Reaction Role: add / remove + Events
# Restricted to manage_roles users
# ---------------------------
@app_commands.checks.has_permissions(manage_roles=True)
@bot.tree.command(name="reactionrole_add", description="Füge eine Reaction Role hinzu", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(message_id="ID der Nachricht", emoji="Emoji (z.B. 👍)", role="Rolle, die vergeben wird")
async def reactionrole_add(interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role):
    # Der Befehl ist zusätzlich durch die Dekorator-Check eingeschränkt (manage_roles)
    # Wir nehmen an, dass der Command im Kanal der Nachricht ausgeführt wird.
    channel = interaction.channel
    try:
        message = await channel.fetch_message(int(message_id))
    except Exception:
        await interaction.response.send_message("❌ Nachricht nicht gefunden (prüfe ID & Kanal).", ephemeral=True)
        return

    key = f"{channel.id}-{message_id}"
    if key not in reaction_roles:
        reaction_roles[key] = {}

    reaction_roles[key][str(emoji)] = role.id
    save_reaction_roles()

    try:
        await message.add_reaction(emoji)
    except Exception as e:
        print(f"⚠️ Reaction konnte nicht gesetzt werden: {e}")
        # trotzdem speichern, falls manuell später hinzugefügt werden kann

    await interaction.response.send_message(f"✅ Reaction Role hinzugefügt: {emoji} → {role.mention}", ephemeral=True)

@app_commands.checks.has_permissions(manage_roles=True)
@bot.tree.command(name="reactionrole_remove", description="Entferne eine Reaction Role", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(message_id="ID der Nachricht", emoji="Emoji")
async def reactionrole_remove(interaction: discord.Interaction, message_id: str, emoji: str):
    channel = interaction.channel
    key = f"{channel.id}-{message_id}"
    if key not in reaction_roles or str(emoji) not in reaction_roles[key]:
        await interaction.response.send_message("❌ Diese Reaction Role existiert nicht.", ephemeral=True)
        return

    try:
        message = await channel.fetch_message(int(message_id))
        # Entferne die Reaction des Bots (falls gesetzt)
        try:
            await message.remove_reaction(emoji, bot.user)
        except Exception:
            pass
    except Exception:
        # Nachricht nicht gefunden oder kein Zugriff
        pass

    del reaction_roles[key][str(emoji)]
    if not reaction_roles[key]:
        del reaction_roles[key]
    save_reaction_roles()
    await interaction.response.send_message(f"✅ Reaction Role {emoji} wurde entfernt.", ephemeral=True)

# Raw reaction events: füge/entferne Rollen bei Reaktionen
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    # Ignoriere Bot-Selbstreaktionen
    if payload.user_id == bot.user.id:
        return
    key = f"{payload.channel_id}-{payload.message_id}"
    if key not in reaction_roles:
        return
    emoji_str = str(payload.emoji)
    if emoji_str not in reaction_roles[key]:
        return
    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return
    role_id = reaction_roles[key][emoji_str]
    role = guild.get_role(role_id)
    member = guild.get_member(payload.user_id)
    if role and member:
        try:
            await member.add_roles(role, reason="Reaction Role added")
        except Exception as e:
            print(f"⚠️ Fehler beim Hinzufügen der Rolle: {e}")

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    key = f"{payload.channel_id}-{payload.message_id}"
    if key not in reaction_roles:
        return
    emoji_str = str(payload.emoji)
    if emoji_str not in reaction_roles[key]:
        return
    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return
    role_id = reaction_roles[key][emoji_str]
    role = guild.get_role(role_id)
    member = guild.get_member(payload.user_id)
    if role and member:
        try:
            await member.remove_roles(role, reason="Reaction Role removed")
        except Exception as e:
            print(f"⚠️ Fehler beim Entfernen der Rolle: {e}")

# ---------------------------
# Reddit Meme Fetcher (aiohttp, nur Bild-Posts) + persistent last_seen
# ---------------------------
async def fetch_reddit_once(test: bool = False):
    """
    Führt genau einen Abruf aus:
    - wenn test=True: postet neue (oder bei initial leerer cache den neuesten Bildpost) ohne Cache zu aktualisieren
    - wenn test=False: postet nur Posts neuer als last_seen_post und aktualisiert am Ende last_seen_post
    """
    global last_seen_post
    print(f"[{datetime.utcnow().isoformat()}] 🔍 Starte Reddit-Abruf (test={test})...")

    if MEME_CHANNEL_ID == 0 or not REDDIT_USER_AGENT:
        print("⚠️ MEME_CHANNEL_ID oder REDDIT_USER_AGENT fehlt.")
        return

    url = "https://www.reddit.com/r/deutschememes/hot.json?limit=10"
    headers = {"User-Agent": REDDIT_USER_AGENT}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers) as resp:
                print(f"📡 Anfrage an Reddit gesendet: Status {resp.status}")
                if resp.status != 200:
                    print(f"❌ Fehler beim Abrufen von Reddit: Status {resp.status}")
                    return
                data = await resp.json()
        except Exception as e:
            print(f"❌ HTTP-Fehler beim Abruf: {e}")
            return

    posts = data.get("data", {}).get("children", [])
    print(f"📦 {len(posts)} Posts empfangen (raw).")

    # Finde die Bild-Posts, neueste zuerst (Reddit liefert z.T. newest->oldest)
    # Wir wollen: nur Posts, die neuer sind als last_seen_post
    new_posts = []

    # Wenn kein last_seen_post gesetzt:
    if last_seen_post is None:
        if test:
            # im Testmodus: finde den neuesten Bildpost und sende ihn (aber ändere cache nicht)
            for p in posts:
                pd = p.get("data", {})
                if pd.get("post_hint") == "image" and pd.get("url"):
                    new_posts.append(pd)
                    break
        else:
            # normaler Lauf beim ersten Start: setze last_seen_post auf neuesten Post und poste nichts
            if posts:
                newest = posts[0].get("data", {}).get("id")
                if newest:
                    save_last_seen(newest)
                    print(f"ℹ️ Initialer Lauf: last_seen_post gesetzt auf {newest}. Keine Posts werden gesendet.")
            return

    else:
        # last_seen_post gesetzt -> sammle alle Bild-Posts, die vor last_seen_post kommen
        for p in posts:
            pd = p.get("data", {})
            pid = pd.get("id")
            if pid == last_seen_post:
                # wir haben bereits alle neuen Beiträge gesammelt
                break
            if pd.get("post_hint") == "image" and pd.get("url"):
                new_posts.append(pd)

    # Posts werden aktuell in Reddit-Reihenfolge (neueste zuerst). Wir möchten von alt -> neu posten:
    new_posts = list(reversed(new_posts))

    if not new_posts:
        print("ℹ️ Keine neuen Bild-Posts zum Posten.")
        return

    channel = bot.get_channel(MEME_CHANNEL_ID)
    if channel is None:
        print("⚠️ Meme Channel nicht gefunden oder Bot hat keinen Zugriff.")
        return

    posted_count = 0
    for pd in new_posts:
        title = pd.get("title", "[kein Titel]")
        url_img = pd.get("url")
        try:
            await channel.send(f"**{title}**\n{url_img}")
            posted_count += 1
            print(f"✅ Gesendet: {title} | {url_img}")
        except Exception as e:
            print(f"❌ Fehler beim Senden des Posts: {e}")

    # Wenn normaler Lauf (nicht Test), aktualisiere last_seen auf die neueste gesendete Post-ID
    if not test and new_posts:
        newest_sent_id = new_posts[-1].get("id")  # die zuletzt gepostete in unserer Reihenfolge ist die neueste
        if newest_sent_id:
            save_last_seen(newest_sent_id)
            print(f"✅ last_seen_post aktualisiert auf {newest_sent_id}")

    print(f"🏁 Abruf beendet. {posted_count} neue Bilder gesendet.")

# Task: ruft periodisch (alle 30 Minuten) fetch_reddit_once() auf
@tasks.loop(minutes=30)
async def reddit_task():
    await fetch_reddit_once(test=False)
    print(f"⏳ Nächster Reddit-Abruf in 30 Minuten ({datetime.utcnow().isoformat()})")

# Slash-Command: direkter Testaufruf (postet im Testmodus)
@bot.tree.command(name="reddit_test", description="Testet den sofortigen Abruf von Reddit-Memes", guild=discord.Object(id=GUILD_ID))
async def reddit_test(interaction: discord.Interaction):
    await interaction.response.send_message("🚀 Starte Test-Abruf von Reddit-Memes...", ephemeral=True)
    # führe einmalig den fetch im Testmodus aus (ändert den Cache nicht)
    await fetch_reddit_once(test=True)
    await interaction.followup.send("✅ Test-Abruf abgeschlossen. Schau in die Logs für Details.", ephemeral=True)

# --- Start Bot ---
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("❌ Kein DISCORD_TOKEN gefunden in Environment!")
    else:
        bot.run(token)
