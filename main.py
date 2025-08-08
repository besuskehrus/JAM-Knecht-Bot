import os
import json
import threading
from flask import Flask
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from typing import Optional

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

# --- IDs ---
GUILD_ID = int(os.getenv("GUILD_ID"))
TEMP_VC_CATEGORY_ID = int(os.getenv("TEMP_VC_CATEGORY_ID", 0))
CREATE_VC_CHANNEL_ID = int(os.getenv("CREATE_VC_CHANNEL_ID", 0))
REACTION_ROLE_FILE = "reaction_roles.json"

if TEMP_VC_CATEGORY_ID == 0 or CREATE_VC_CHANNEL_ID == 0:
    print("⚠️ Bitte TEMP_VC_CATEGORY_ID und CREATE_VC_CHANNEL_ID als Umgebungsvariablen setzen!")

# --- Temp VC Tracking ---
temp_voice_channels = {}

# --- Reaction Roles laden ---
if not os.path.exists(REACTION_ROLE_FILE):
    with open(REACTION_ROLE_FILE, "w") as f:
        json.dump({}, f)

with open(REACTION_ROLE_FILE, "r") as f:
    reaction_roles = json.load(f)

# --- Events ---
@bot.event
async def on_ready():
    print(f"✅ Bot ist online: {bot.user}")
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"🔁 Slash Commands synchronisiert: {len(synced)}")
    except Exception as e:
        print(f"Fehler beim Synchronisieren: {e}")

@bot.event
async def on_voice_state_update(member, before, after):
    global temp_voice_channels

    if member.bot:
        return

    guild = member.guild

    if after.channel and after.channel.id == CREATE_VC_CHANNEL_ID:
        category = guild.get_channel(TEMP_VC_CATEGORY_ID)
        if category is None:
            print("⚠️ Kategorie für Temp-VC nicht gefunden!")
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(connect=True, view_channel=True),
            member: discord.PermissionOverwrite(manage_channels=True, connect=True, view_channel=True)
        }
        new_vc = await guild.create_voice_channel(
            name=f"Voicechat von {member.display_name}",
            category=category,
            overwrites=overwrites
        )
        temp_voice_channels[member.id] = new_vc.id
        await member.move_to(new_vc)

    if before.channel and before.channel.id in temp_voice_channels.values():
        channel = before.channel
        if len(channel.members) == 0:
            await channel.delete()
            user_to_remove = None
            for uid, cid in temp_voice_channels.items():
                if cid == channel.id:
                    user_to_remove = uid
                    break
            if user_to_remove:
                del temp_voice_channels[user_to_remove]

@bot.event
async def on_raw_reaction_add(payload):
    if str(payload.message_id) in reaction_roles:
        emoji = str(payload.emoji)
        if emoji in reaction_roles[str(payload.message_id)]:
            guild = bot.get_guild(payload.guild_id)
            member = guild.get_member(payload.user_id)
            role_id = reaction_roles[str(payload.message_id)][emoji]
            role = guild.get_role(role_id)
            if role and member:
                await member.add_roles(role)

@bot.event
async def on_raw_reaction_remove(payload):
    if str(payload.message_id) in reaction_roles:
        emoji = str(payload.emoji)
        if emoji in reaction_roles[str(payload.message_id)]:
            guild = bot.get_guild(payload.guild_id)
            member = guild.get_member(payload.user_id)
            role_id = reaction_roles[str(payload.message_id)][emoji]
            role = guild.get_role(role_id)
            if role and member:
                await member.remove_roles(role)

# --- Slash Commands ---
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

@bot.tree.command(name="einladen", description="Gib bestimmten Mitgliedern Zugriff auf deinen Voicechat", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(
    user1="Mitglied 1",
    user2="Mitglied 2",
    user3="Mitglied 3",
    user4="Mitglied 4",
    user5="Mitglied 5"
)
async def einladen(interaction: discord.Interaction,
                   user1: discord.Member,
                   user2: Optional[discord.Member] = None,
                   user3: Optional[discord.Member] = None,
                   user4: Optional[discord.Member] = None,
                   user5: Optional[discord.Member] = None):
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("❌ Du bist in keinem Voice-Channel.", ephemeral=True)
        return

    channel = interaction.user.voice.channel
    users = [u for u in [user1, user2, user3, user4, user5] if u]

    for member in users:
        await channel.set_permissions(member, view_channel=True, connect=True)

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

# --- Reaction Role Commands (admin-only) ---
@app_commands.checks.has_permissions(manage_roles=True)
@bot.tree.command(name="reactionrole_add", description="Füge eine Reaction Role hinzu", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(message_id="ID der Nachricht", emoji="Emoji", role="Rolle", channel="Channel")
async def reactionrole_add(interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role, channel: discord.TextChannel):
    try:
        message = await channel.fetch_message(int(message_id))
        await message.add_reaction(emoji)
        reaction_roles.setdefault(message_id, {})[emoji] = role.id

        with open(REACTION_ROLE_FILE, "w") as f:
            json.dump(reaction_roles, f)

        await interaction.response.send_message(f"✅ Reaction Role hinzugefügt: {emoji} → {role.mention}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Fehler: {e}", ephemeral=True)

@app_commands.checks.has_permissions(manage_roles=True)
@bot.tree.command(name="reactionrole_remove", description="Entferne eine Reaction Role", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(message_id="ID der Nachricht", emoji="Emoji, das entfernt werden soll")
async def reactionrole_remove(interaction: discord.Interaction, message_id: str, emoji: str):
    try:
        if message_id in reaction_roles and emoji in reaction_roles[message_id]:
            del reaction_roles[message_id][emoji]
            if not reaction_roles[message_id]:
                del reaction_roles[message_id]

            with open(REACTION_ROLE_FILE, "w") as f:
                json.dump(reaction_roles, f)

            await interaction.response.send_message("✅ Reaction Role wurde entfernt.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Keine passende Reaction Role gefunden.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Fehler: {e}", ephemeral=True)

# --- Fehlerbehandlung für fehlende Rechte ---
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ Du hast keine Berechtigung für diesen Befehl.", ephemeral=True)

# --- Bot starten ---
bot.run(os.getenv("DISCORD_TOKEN"))
