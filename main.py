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

if TEMP_VC_CATEGORY_ID == 0 or CREATE_VC_CHANNEL_ID == 0:
    print("⚠️ Bitte TEMP_VC_CATEGORY_ID und CREATE_VC_CHANNEL_ID als Umgebungsvariablen setzen!")

# Mapping: User ID -> Temp Voice Channel ID
temp_voice_channels = {}

# --- On Ready ---
@bot.event
async def on_ready():
    print(f"✅ Bot ist online: {bot.user}")
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"🔁 Slash Commands synchronisiert: {len(synced)}")
    except Exception as e:
        print(f"Fehler beim Synchronisieren: {e}")

# --- Voice State Update: Temp VC Logik ---
@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return  # Bots ignorieren

    guild = member.guild

    # Temp VC erstellen, wenn User dem "Create Voice" Channel beitritt
    if after.channel and after.channel.id == CREATE_VC_CHANNEL_ID:
        category = guild.get_channel(TEMP_VC_CATEGORY_ID)
        if category is None:
            print("⚠️ Kategorie für Temp-VC nicht gefunden!")
            return

        # Erstelle neuen Voice Channel mit Namen "Voicechat von [USER]"
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

        # User in den neuen Voice-Channel verschieben
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

# --- Beispiel: Slash Command zum Verbergen / Freigeben (Platzhalter, erweitern nach Wunsch) ---
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

# --- Slash Command /jam ---
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

# --- Webserver bereits gestartet via Thread ---

# --- Bot starten ---
bot.run(os.getenv("DISCORD_TOKEN"))