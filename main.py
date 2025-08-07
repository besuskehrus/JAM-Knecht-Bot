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

# Guild ID aus Umgebungsvariable holen
GUILD_ID = int(os.getenv("GUILD_ID"))

# --- On Ready ---
@bot.event
async def on_ready():
    print(f"✅ Bot ist online: {bot.user}")
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"🔁 Slash Commands synchronisiert: {len(synced)}")
    except Exception as e:
        print(f"Fehler beim Synchronisieren: {e}")

# --- Voice-Event: erkennt VC-Join ---
@bot.event
async def on_voice_state_update(member, before, after):
    if after.channel and (before.channel != after.channel) and not member.bot:
        channel_name = after.channel.name
        text_channel = discord.utils.get(member.guild.text_channels, name="jam-links")

        if text_channel:
            await text_channel.send(f"{member.mention} ist dem VC **{channel_name}** beigetreten 🎧\n"
                                    f"Bitte teile deinen Spotify Jam-Link mit `/jam`.")

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

# --- Bot starten ---
bot.run(os.getenv("DISCORD_TOKEN"))