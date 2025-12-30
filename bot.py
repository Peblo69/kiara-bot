import discord
from discord import option
from discord.ui import View, Button, Select, Modal, InputText
import asyncio
import io
import base64
import aiohttp
import os
from datetime import datetime, timezone, timedelta
import logging

# Logging setup - use DEBUG_VOICE=1 env var for verbose logging
_debug_voice = os.getenv("DEBUG_VOICE", "0") == "1"
logging.basicConfig(
    level=logging.DEBUG if _debug_voice else logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    datefmt='%H:%M:%S'
)

if _debug_voice:
    # File logger for persistent voice debugging (local only)
    _log_path = os.path.join(os.path.dirname(__file__), "voice_run.log")
    _file_handler = logging.FileHandler(_log_path, encoding="utf-8")
    _file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    logging.getLogger().addHandler(_file_handler)
    # Set specific loggers to DEBUG for voice troubleshooting
    for logger_name in ["discord", "discord.voice_client", "discord.gateway", "discord.client", "discord.voice_state"]:
        logging.getLogger(logger_name).setLevel(logging.DEBUG)
    for logger_name in ["voice.session", "voice.sink", "voice.playback", "voice.gemini", "voice.ptt", "voice.state", "discord.player"]:
        logging.getLogger(logger_name).setLevel(logging.DEBUG)

from config import (
    DISCORD_TOKEN,
    DAILY_LIMIT,
    RATE_LIMIT_RPM,
    BOT_NAME,
    BOT_COLOR,
    VOICE_PTT_ENABLED,
    VOICE_PTT_KEY,
)
from database import (
    init_db, get_or_create_user, get_daily_usage, increment_usage,
    get_user_settings, update_user_settings,
    save_reference_image, get_reference_images, delete_reference_image, clear_all_references,
    save_user_channel, get_user_channel
)
from imagen import imagen
from rate_limiter import RateLimitedQueue

# Voice AI imports
try:
    from voice.session_manager import VoiceSessionManager, init_voice_manager, get_voice_manager
    VOICE_AVAILABLE = True
except ImportError as e:
    print(f"[WARNING] Voice module not available: {e}")
    VOICE_AVAILABLE = False

try:
    from voice.ptt_listener import PushToTalkListener
    PTT_AVAILABLE = True
except ImportError as e:
    print(f"[WARNING] PTT listener not available: {e}")
    PTT_AVAILABLE = False

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True  # Required for voice
intents.guilds = True
intents.members = True  # For voice channel member info
bot = discord.Bot(intents=intents, auto_sync_commands=False)

@bot.event
async def on_application_command_error(ctx, error):
    print(f">>> COMMAND ERROR: {error}", flush=True)
    import traceback
    traceback.print_exc()

# Global queue
queue = RateLimitedQueue(requests_per_minute=RATE_LIMIT_RPM)

# Track users waiting to upload
upload_waiters = {}  # user_id -> channel_id

# Push-to-talk listener (kept alive)
ptt_listener = None

# Model options - Gemini image models (work globally)
MODELS = {
    "Gemini 3 Pro": "gemini-3-pro-image-preview",
    "Gemini 2.5 Flash": "gemini-2.5-flash-image",
    "Gemini 2.5 Preview": "gemini-2.5-flash-image-preview",
}

QUALITIES = ["1K", "2K", "4K"]

ASPECT_RATIOS = {
    "1:1 Square": "1:1",
    "16:9 Landscape": "16:9",
    "9:16 Portrait": "9:16",
    "4:3 Standard": "4:3",
    "3:4 Portrait": "3:4",
    "21:9 Ultrawide": "21:9",
}

# Style presets - added to prompt automatically
STYLE_PRESETS = {
    "none": "",
    "photorealistic": ", ultra realistic, 8k uhd, photorealistic, professional photography, natural lighting",
    "anime": ", anime style, studio ghibli inspired, cel shaded, vibrant colors, detailed anime art",
    "cyberpunk": ", cyberpunk style, neon lights, futuristic, blade runner aesthetic, rain, night city",
    "fantasy": ", fantasy art style, magical, ethereal, detailed illustration, epic fantasy",
    "oil_painting": ", oil painting style, classical art, brushstrokes visible, museum quality",
    "watercolor": ", watercolor painting, soft edges, artistic, delicate colors, paper texture",
    "3d_render": ", 3D render, octane render, unreal engine 5, highly detailed, volumetric lighting",
    "comic": ", comic book style, bold lines, dynamic, superhero aesthetic, vibrant",
    "minimalist": ", minimalist style, clean, simple, elegant, white space, modern design",
}


def get_time_until_reset() -> str:
    """Get human-readable time until midnight UTC reset"""
    now = datetime.now(timezone.utc)
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    diff = tomorrow - now

    hours = int(diff.total_seconds() // 3600)
    minutes = int((diff.total_seconds() % 3600) // 60)

    if hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"


# ============== HELPERS ==============

async def download_image(url: str) -> tuple[bytes, str]:
    """Download image and return bytes + mime type"""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.read()
                content_type = resp.headers.get('content-type', 'image/png')
                return data, content_type
    raise Exception("Failed to download image")


def create_panel_embed(user_id: int, settings: dict, refs: list, status: str = None, usage: int = 0) -> discord.Embed:
    """Create the main control panel embed - advanced look"""
    embed = discord.Embed(
        title=f"✨ {BOT_NAME}",
        color=BOT_COLOR
    )

    # Calculate remaining and reset time
    remaining = DAILY_LIMIT - usage
    reset_time = get_time_until_reset()

    # Status bar at top
    if status:
        embed.description = f"```\n{status}\n```"
    else:
        if remaining > 0:
            embed.description = f"```\n🟢 Ready • {remaining} generations left • Resets in {reset_time}\n```"
        else:
            embed.description = f"```\n🔴 Limit reached • Resets in {reset_time}\n```"

    # Current configuration
    model_name = next((k for k, v in MODELS.items() if v == settings.get("model")), "Gemini 3 Pro")
    quality = settings.get("quality", "1K")
    aspect = settings.get("aspect_ratio", "1:1")
    style = settings.get("style", "none")
    style_display = style.replace("_", " ").title() if style != "none" else "None"

    config_text = (
        f"🤖 **{model_name}**\n"
        f"📐 {quality} • {aspect}\n"
        f"🎨 Style: {style_display}"
    )
    embed.add_field(name="⚙️ Configuration", value=config_text, inline=True)

    # Reference images
    ref_count = len(refs)
    if ref_count > 0:
        ref_lines = [f"• `{r['filename'][:18]}`" for r in refs[:5]]
        ref_text = "\n".join(ref_lines)
    else:
        ref_text = "*No references*\nUpload for style/\nface transfer"

    embed.add_field(name=f"📷 Refs ({ref_count}/5)", value=ref_text, inline=True)

    # Usage stats
    stats_text = (
        f"📊 **{usage}/{DAILY_LIMIT}** today\n"
        f"⏰ Reset: {reset_time}"
    )
    embed.add_field(name="📈 Usage", value=stats_text, inline=True)

    embed.set_footer(text="Select options below • Click ✨ GENERATE when ready")

    return embed


# ============== VIEWS ==============

class MainPanelView(View):
    """Main control panel with dropdowns and buttons"""

    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

    # Row 0: Action buttons
    @discord.ui.button(label="📤 Upload Refs", style=discord.ButtonStyle.secondary, row=0)
    async def upload_button(self, button: Button, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel!", ephemeral=True)
            return

        upload_waiters[self.user_id] = {
            "channel_id": interaction.channel_id,
            "panel_message": interaction.message
        }

        settings = await get_user_settings(self.user_id)
        refs = await get_reference_images(self.user_id)
        usage = await get_daily_usage(self.user_id)
        embed = create_panel_embed(self.user_id, settings, refs, status="📤 DROP IMAGES HERE • Type 'done' when finished", usage=usage)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🗑️ Clear", style=discord.ButtonStyle.secondary, row=0)
    async def clear_button(self, button: Button, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel!", ephemeral=True)
            return

        await clear_all_references(self.user_id)
        settings = await get_user_settings(self.user_id)
        usage = await get_daily_usage(self.user_id)
        embed = create_panel_embed(self.user_id, settings, [], usage=usage)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="✨ GENERATE", style=discord.ButtonStyle.success, row=0)
    async def generate_button(self, button: Button, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel!", ephemeral=True)
            return

        await get_or_create_user(interaction.user.id, interaction.user.name)
        usage = await get_daily_usage(interaction.user.id)

        if usage >= DAILY_LIMIT:
            settings = await get_user_settings(self.user_id)
            refs = await get_reference_images(self.user_id)
            embed = create_panel_embed(self.user_id, settings, refs, status="⛔ DAILY LIMIT REACHED • Resets at midnight UTC", usage=usage)
            await interaction.response.edit_message(embed=embed, view=self)
            return

        modal = PromptModal(self.user_id, panel_message=interaction.message)
        await interaction.response.send_modal(modal)

    # Row 1: Model dropdown
    @discord.ui.select(
        placeholder="🤖 Select Model",
        options=[
            discord.SelectOption(label="Gemini 3 Pro", value="gemini-3-pro-image-preview", description="Best quality", emoji="⭐"),
            discord.SelectOption(label="Gemini 2.5 Flash", value="gemini-2.5-flash-image", description="Fast generation", emoji="⚡"),
            discord.SelectOption(label="Gemini 2.5 Preview", value="gemini-2.5-flash-image-preview", description="Experimental", emoji="🧪"),
        ],
        row=1
    )
    async def model_select(self, select: Select, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel!", ephemeral=True)
            return

        await update_user_settings(self.user_id, model=select.values[0])
        settings = await get_user_settings(self.user_id)
        refs = await get_reference_images(self.user_id)
        usage = await get_daily_usage(self.user_id)
        embed = create_panel_embed(self.user_id, settings, refs, usage=usage)
        await interaction.response.edit_message(embed=embed, view=self)

    # Row 2: Quality dropdown
    @discord.ui.select(
        placeholder="📐 Select Quality",
        options=[
            discord.SelectOption(label="1K (1024px)", value="1K", description="Standard quality", emoji="1️⃣"),
            discord.SelectOption(label="2K (2048px)", value="2K", description="High quality", emoji="2️⃣"),
            discord.SelectOption(label="4K (4096px)", value="4K", description="Ultra quality", emoji="4️⃣"),
        ],
        row=2
    )
    async def quality_select(self, select: Select, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel!", ephemeral=True)
            return

        await update_user_settings(self.user_id, quality=select.values[0])
        settings = await get_user_settings(self.user_id)
        refs = await get_reference_images(self.user_id)
        usage = await get_daily_usage(self.user_id)
        embed = create_panel_embed(self.user_id, settings, refs, usage=usage)
        await interaction.response.edit_message(embed=embed, view=self)

    # Row 3: Aspect ratio dropdown
    @discord.ui.select(
        placeholder="🖼️ Select Aspect Ratio",
        options=[
            discord.SelectOption(label="1:1 Square", value="1:1", emoji="⬜"),
            discord.SelectOption(label="16:9 Landscape", value="16:9", emoji="🖥️"),
            discord.SelectOption(label="9:16 Portrait", value="9:16", emoji="📱"),
            discord.SelectOption(label="4:3 Standard", value="4:3", emoji="📺"),
            discord.SelectOption(label="3:4 Portrait", value="3:4", emoji="🖼️"),
            discord.SelectOption(label="21:9 Ultrawide", value="21:9", emoji="🎬"),
        ],
        row=3
    )
    async def aspect_select(self, select: Select, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel!", ephemeral=True)
            return

        await update_user_settings(self.user_id, aspect_ratio=select.values[0])
        settings = await get_user_settings(self.user_id)
        refs = await get_reference_images(self.user_id)
        usage = await get_daily_usage(self.user_id)
        embed = create_panel_embed(self.user_id, settings, refs, usage=usage)
        await interaction.response.edit_message(embed=embed, view=self)

    # Row 4: Style preset dropdown
    @discord.ui.select(
        placeholder="🎨 Select Style Preset",
        options=[
            discord.SelectOption(label="None (Raw Prompt)", value="none", emoji="⚪"),
            discord.SelectOption(label="Photorealistic", value="photorealistic", emoji="📷"),
            discord.SelectOption(label="Anime", value="anime", emoji="🎌"),
            discord.SelectOption(label="Cyberpunk", value="cyberpunk", emoji="🌃"),
            discord.SelectOption(label="Fantasy", value="fantasy", emoji="🧙"),
            discord.SelectOption(label="Oil Painting", value="oil_painting", emoji="🖼️"),
            discord.SelectOption(label="Watercolor", value="watercolor", emoji="🎨"),
            discord.SelectOption(label="3D Render", value="3d_render", emoji="💎"),
            discord.SelectOption(label="Comic Book", value="comic", emoji="💥"),
            discord.SelectOption(label="Minimalist", value="minimalist", emoji="◽"),
        ],
        row=4
    )
    async def style_select(self, select: Select, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel!", ephemeral=True)
            return

        await update_user_settings(self.user_id, style=select.values[0])
        settings = await get_user_settings(self.user_id)
        refs = await get_reference_images(self.user_id)
        usage = await get_daily_usage(self.user_id)
        embed = create_panel_embed(self.user_id, settings, refs, usage=usage)
        await interaction.response.edit_message(embed=embed, view=self)


# ============== WELCOME / STUDIO SYSTEM ==============

class WelcomeView(View):
    """Welcome message with Create Studio button"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create My Studio", style=discord.ButtonStyle.success, emoji="🎨", custom_id="create_studio")
    async def create_studio_button(self, button: Button, interaction: discord.Interaction):
        user = interaction.user
        guild = interaction.guild

        if not guild:
            await interaction.response.send_message("This only works in a server!", ephemeral=True)
            return

        # Defer IMMEDIATELY to avoid timeout
        await interaction.response.defer(ephemeral=True)

        # Check if user already has a studio
        existing_channel_id = await get_user_channel(user.id, guild.id)
        if existing_channel_id:
            existing_channel = guild.get_channel(existing_channel_id)
            if existing_channel:
                await interaction.followup.send(
                    f"You already have a studio! Go to {existing_channel.mention}",
                    ephemeral=True
                )
                return

        try:
            # Find or create "Studios" category
            category = discord.utils.get(guild.categories, name="Studios")
            if not category:
                category = await guild.create_category(
                    "Studios",
                    overwrites={
                        guild.default_role: discord.PermissionOverwrite(view_channel=False)
                    }
                )

            # Create private channel for user
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                user: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    attach_files=True,
                    embed_links=True,
                    read_message_history=True
                ),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    attach_files=True,
                    embed_links=True,
                    manage_messages=True
                )
            }

            # Add admin role permissions if exists
            admin_role = discord.utils.get(guild.roles, name="Admin")
            if admin_role:
                overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

            channel = await guild.create_text_channel(
                f"studio-{user.name.lower().replace(' ', '-')}",
                category=category,
                overwrites=overwrites,
                topic=f"Private studio for {user.name} | Use /panel to start generating"
            )

            # Save to database
            await save_user_channel(user.id, channel.id, guild.id)
            await get_or_create_user(user.id, user.name)

            # Send welcome message in the new channel
            settings = await get_user_settings(user.id)
            refs = await get_reference_images(user.id)
            embed = create_panel_embed(user.id, settings, refs, status="🎉 Studio ready! Click GENERATE to start")

            panel_msg = await channel.send(
                f"# 🎨 Welcome, {user.mention}!\n"
                f"Your **private studio** — only you can see this channel.\n"
                f"All images reply to this panel — click the reply to jump back here!",
                embed=embed,
                view=MainPanelView(user.id)
            )

            # Save panel message ID
            await update_user_settings(user.id, panel_message_id=panel_msg.id, panel_channel_id=channel.id)

            await interaction.followup.send(
                f"✅ Your private studio has been created! Go to {channel.mention}",
                ephemeral=True
            )

        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I don't have permission to create channels. Ask an admin to fix my permissions.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)[:200]}", ephemeral=True)


class ImageActionView(View):
    """Minimal buttons on generated images"""

    def __init__(self, user_id: int, prompt: str, panel_message_id: int = None):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.prompt = prompt
        self.panel_message_id = panel_message_id

    @discord.ui.button(label="🔄 Again", style=discord.ButtonStyle.secondary, row=0)
    async def regenerate_button(self, button: Button, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your image!", ephemeral=True)
            return

        usage = await get_daily_usage(self.user_id)
        if usage >= DAILY_LIMIT:
            await interaction.response.send_message(f"⏰ Limit reached!", ephemeral=True)
            return

        # Update panel to show generating
        settings = await get_user_settings(self.user_id)
        refs = await get_reference_images(self.user_id)
        channel = interaction.channel

        # Find and update panel
        panel_msg = None
        if settings.get("panel_message_id"):
            try:
                panel_msg = await channel.fetch_message(settings["panel_message_id"])
                embed = create_panel_embed(self.user_id, settings, refs, status="🔄 Regenerating...")
                await panel_msg.edit(embed=embed)
            except:
                pass

        await interaction.response.defer()

        try:
            ref_images = [{"base64": r["image_data"], "mimeType": r["mime_type"]} for r in refs]

            image_bytes = await imagen.generate_with_refs(
                prompt=self.prompt,
                reference_images=ref_images,
                aspect_ratio=settings["aspect_ratio"],
                quality=settings["quality"],
                model=settings["model"]
            )

            await increment_usage(self.user_id)

            # Send image as reply to panel (so clicking jumps to panel)
            file = discord.File(io.BytesIO(image_bytes), filename="generated.jpg")
            await channel.send(
                content=f"`{self.prompt[:80]}`",
                file=file,
                view=ImageActionView(self.user_id, self.prompt, settings.get("panel_message_id")),
                reference=panel_msg if panel_msg else None
            )

            # Update panel to ready state
            if panel_msg:
                new_usage = await get_daily_usage(self.user_id)
                embed = create_panel_embed(self.user_id, settings, refs, status=f"✅ Done! ({new_usage}/{DAILY_LIMIT} today)")
                await panel_msg.edit(embed=embed)

        except Exception as e:
            if panel_msg:
                embed = create_panel_embed(self.user_id, settings, refs, status=f"❌ {str(e)[:50]}")
                await panel_msg.edit(embed=embed)


class PromptModal(Modal):
    """Modal for entering generation prompt"""

    def __init__(self, user_id: int, panel_message: discord.Message = None):
        super().__init__(title="✨ Generate Image")
        self.user_id = user_id
        self.panel_message = panel_message

        self.prompt_input = InputText(
            label="Describe your image",
            placeholder="A futuristic city at sunset with flying cars...",
            style=discord.InputTextStyle.paragraph,
            max_length=1000,
            required=True
        )
        self.add_item(self.prompt_input)

    async def callback(self, interaction: discord.Interaction):
        prompt = self.prompt_input.value
        channel = interaction.channel

        settings = await get_user_settings(self.user_id)
        refs = await get_reference_images(self.user_id)
        usage = await get_daily_usage(self.user_id)

        # Apply style preset to prompt
        style_key = settings.get("style", "none")
        style_suffix = STYLE_PRESETS.get(style_key, "")
        full_prompt = prompt + style_suffix

        # Get display names
        model_name = next((k for k, v in MODELS.items() if v == settings.get("model")), "Gemini")
        style_name = style_key.replace("_", " ").title() if style_key != "none" else "None"

        # Update panel to show generating status
        if self.panel_message:
            gen_status = (
                f"🎨 GENERATING YOUR IMAGE...\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📝 {prompt[:45]}{'...' if len(prompt) > 45 else ''}\n"
                f"🤖 {model_name} • {settings.get('quality', '1K')} • {settings.get('aspect_ratio', '1:1')}\n"
                f"🎨 Style: {style_name} • 📷 {len(refs)} ref(s)"
            )
            embed = create_panel_embed(self.user_id, settings, refs, status=gen_status, usage=usage)
            await interaction.response.edit_message(embed=embed, view=MainPanelView(self.user_id))
        else:
            await interaction.response.defer()

        try:
            ref_images = [{"base64": r["image_data"], "mimeType": r["mime_type"]} for r in refs]

            image_bytes = await imagen.generate_with_refs(
                prompt=full_prompt,  # Use prompt with style
                reference_images=ref_images,
                aspect_ratio=settings["aspect_ratio"],
                quality=settings["quality"],
                model=settings["model"]
            )

            await increment_usage(self.user_id)

            # Send image as REPLY to panel
            file = discord.File(io.BytesIO(image_bytes), filename="generated.jpg")
            panel_id = self.panel_message.id if self.panel_message else None

            await channel.send(
                content=f"`{prompt[:80]}`",
                file=file,
                view=ImageActionView(self.user_id, prompt, panel_id),
                reference=self.panel_message
            )

            # Update panel to success state
            if self.panel_message:
                new_usage = await get_daily_usage(self.user_id)
                embed = create_panel_embed(self.user_id, settings, refs, usage=new_usage)
                await self.panel_message.edit(embed=embed, view=MainPanelView(self.user_id))
                await update_user_settings(self.user_id, panel_message_id=self.panel_message.id, panel_channel_id=channel.id)

        except Exception as e:
            if self.panel_message:
                error_status = f"❌ GENERATION FAILED\n━━━━━━━━━━━━━━━━━━━━━━━━━━━\n{str(e)[:60]}"
                embed = create_panel_embed(self.user_id, settings, refs, status=error_status, usage=usage)
                await self.panel_message.edit(embed=embed, view=MainPanelView(self.user_id))


# ============== EVENTS ==============

@bot.event
async def on_ready():
    print(f"{'='*50}")
    print(f"  {BOT_NAME} is online!")
    print(f"  Logged in as: {bot.user}")
    print(f"  Servers: {len(bot.guilds)}")
    print(f"  Rate limit: {RATE_LIMIT_RPM} RPM")
    print(f"  Daily limit: {DAILY_LIMIT} per user")
    print(f"  Voice AI: {'ENABLED' if VOICE_AVAILABLE else 'DISABLED'}")
    print(f"{'='*50}")

    await init_db()
    await queue.start()

    # Initialize voice manager
    if VOICE_AVAILABLE:
        voice_mgr = init_voice_manager(bot)
        print("[Voice] Voice session manager initialized")

        if VOICE_PTT_ENABLED:
            voice_mgr.enable_ptt(True)
            if PTT_AVAILABLE:
                global ptt_listener
                ptt_listener = PushToTalkListener(bot, voice_mgr, VOICE_PTT_KEY)
                if ptt_listener.start():
                    print(f"[Voice] Push-to-talk enabled on key: {VOICE_PTT_KEY}")
                else:
                    print("[Voice] Push-to-talk enabled but keyboard hook unavailable")
            else:
                print("[Voice] Push-to-talk enabled but listener not available")

    # Register persistent views (survive bot restarts)
    bot.add_view(WelcomeView())

    # Skip command sync on startup to avoid rate limits
    # Commands are auto-synced by py-cord
    print("Bot ready - skipping manual command sync")


@bot.event
async def on_message(message: discord.Message):
    """Handle image uploads when user is in upload mode"""
    if message.author.bot:
        return

    user_id = message.author.id

    # Check if user is in upload mode
    if user_id not in upload_waiters:
        return

    waiter = upload_waiters[user_id]
    if waiter["channel_id"] != message.channel.id:
        return

    panel_message = waiter.get("panel_message")

    # Check for cancel/done commands
    content = message.content.lower().strip()
    if content == "cancel":
        del upload_waiters[user_id]
        # Update panel
        if panel_message:
            settings = await get_user_settings(user_id)
            refs = await get_reference_images(user_id)
            embed = create_panel_embed(user_id, settings, refs, status="❌ Upload cancelled")
            try:
                await panel_message.edit(embed=embed, view=MainPanelView(user_id))
            except:
                pass
        try:
            await message.delete()
        except:
            pass
        return

    if content == "done":
        del upload_waiters[user_id]
        refs = await get_reference_images(user_id)
        # Update panel
        if panel_message:
            settings = await get_user_settings(user_id)
            embed = create_panel_embed(user_id, settings, refs, status=f"✅ {len(refs)} reference(s) ready")
            try:
                await panel_message.edit(embed=embed, view=MainPanelView(user_id))
            except:
                pass
        try:
            await message.delete()
        except:
            pass
        return

    # Process attachments
    if not message.attachments:
        return

    refs = await get_reference_images(user_id)
    current_count = len(refs)

    for attachment in message.attachments:
        if current_count >= 5:
            break

        if not attachment.content_type or not attachment.content_type.startswith("image/"):
            continue

        try:
            img_data, mime_type = await download_image(attachment.url)
            b64_data = base64.b64encode(img_data).decode("utf-8")

            used_slots = [r["slot"] for r in refs]
            next_slot = next(i for i in range(1, 6) if i not in used_slots)

            await save_reference_image(
                user_id=user_id,
                slot=next_slot,
                image_data=b64_data,
                mime_type=mime_type,
                filename=attachment.filename
            )

            current_count += 1
            refs = await get_reference_images(user_id)

        except:
            pass

    # Delete the upload message (keep chat clean!)
    deleted = False
    try:
        await message.delete()
        deleted = True
    except discord.Forbidden:
        # Bot lacks permission - can't delete
        pass
    except:
        pass

    # Update panel with new count
    if panel_message:
        settings = await get_user_settings(user_id)
        if current_count >= 5:
            del upload_waiters[user_id]
            status = f"✅ {current_count}/5 refs — Ready to generate!"
        else:
            status = f"📤 {current_count}/5 uploaded — Drop more or type `done`"

        if not deleted and current_count > 0:
            status += " ⚠️"  # Indicate couldn't delete

        embed = create_panel_embed(user_id, settings, refs, status=status)
        try:
            await panel_message.edit(embed=embed, view=MainPanelView(user_id))
        except:
            pass


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if not VOICE_AVAILABLE or not bot.user:
        return

    logger = logging.getLogger("voice.state")

    def _fmt_channel(state: discord.VoiceState) -> str:
        if not state or not state.channel:
            return "None"
        return f"{state.channel.name} ({state.channel.id})"

    if member.id == bot.user.id:
        logger.info(
            "Bot voice state: guild=%s before=%s after=%s",
            member.guild.id if member.guild else None,
            _fmt_channel(before),
            _fmt_channel(after),
        )
        return

    voice_mgr = get_voice_manager()
    if voice_mgr and voice_mgr.is_ptt_enabled():
        owner = voice_mgr.get_ptt_owner()
        if owner and member.id == owner[1]:
            logger.info(
                "PTT owner voice state: user=%s before=%s after=%s",
                member.id,
                _fmt_channel(before),
                _fmt_channel(after),
            )


@bot.event
async def on_voice_server_update(data):
    logging.getLogger("voice.state").info("Voice server update: %s", data)


@bot.event
async def on_disconnect():
    logging.getLogger("discord.client").warning("Discord websocket disconnected")


@bot.event
async def on_resumed():
    logging.getLogger("discord.client").info("Discord websocket resumed")


# ============== COMMANDS ==============

@bot.slash_command(name="imagine", description="Open Kiara AI image generation")
async def panel_command(ctx: discord.ApplicationContext):
    """Spawn the control panel"""
    user_id = ctx.author.id

    await get_or_create_user(user_id, ctx.author.name)
    settings = await get_user_settings(user_id)
    refs = await get_reference_images(user_id)

    usage = await get_daily_usage(user_id)
    embed = create_panel_embed(user_id, settings, refs, usage=usage)

    msg = await ctx.respond(embed=embed, view=MainPanelView(user_id))

    # Save panel message ID
    if hasattr(msg, 'id'):
        await update_user_settings(user_id, panel_message_id=msg.id, panel_channel_id=ctx.channel.id)


@bot.slash_command(name="quick", description="Quick generate without panel")
@option("prompt", str, description="Describe your image", required=True)
async def quick_command(ctx: discord.ApplicationContext, prompt: str):
    """Quick generation with default settings"""
    user_id = ctx.author.id

    await get_or_create_user(user_id, ctx.author.name)
    usage = await get_daily_usage(user_id)

    if usage >= DAILY_LIMIT:
        await ctx.respond(f"⏰ Daily limit reached ({DAILY_LIMIT}).", ephemeral=True)
        return

    await ctx.defer()

    settings = await get_user_settings(user_id)
    refs = await get_reference_images(user_id)

    try:
        ref_images = [{"base64": r["image_data"], "mimeType": r["mime_type"]} for r in refs]

        image_bytes = await imagen.generate_with_refs(
            prompt=prompt,
            reference_images=ref_images,
            aspect_ratio=settings["aspect_ratio"],
            quality=settings["quality"],
            model=settings["model"]
        )

        await increment_usage(user_id)

        file = discord.File(io.BytesIO(image_bytes), filename="generated.png")
        embed = discord.Embed(title="✨ Generated", color=BOT_COLOR)
        embed.add_field(name="Prompt", value=prompt[:1024], inline=False)
        embed.set_image(url="attachment://generated.png")

        await ctx.followup.send(embed=embed, file=file)

    except Exception as e:
        await ctx.followup.send(f"❌ Failed: {str(e)[:200]}")


# ============== ADMIN COMMANDS ==============

@bot.slash_command(name="setup", description="[Admin] Setup the welcome message with Create Studio button")
@discord.default_permissions(administrator=True)
async def setup_command(ctx: discord.ApplicationContext):
    """Admin command to setup the welcome message"""
    embed = discord.Embed(
        title=f"🎨 Welcome to {BOT_NAME}!",
        description=(
            "**Free AI Image Generation powered by Google Gemini**\n\n"
            "✨ **20 free generations per day**\n"
            "🖼️ High quality 2K images\n"
            "📷 Use reference images for style/face transfer\n"
            "🔒 **100% Private** - Your own studio channel\n\n"
            "Click the button below to create your private studio!"
        ),
        color=BOT_COLOR
    )
    embed.set_footer(text="Your studio is private - only you and admins can see it")

    await ctx.respond(embed=embed, view=WelcomeView())


@bot.slash_command(name="fixperms", description="[Admin] Fix bot permissions in this channel")
@discord.default_permissions(administrator=True)
async def fixperms_command(ctx: discord.ApplicationContext):
    """Grant bot manage_messages in current channel"""
    try:
        await ctx.channel.set_permissions(
            ctx.guild.me,
            manage_messages=True,
            send_messages=True,
            attach_files=True,
            embed_links=True,
            view_channel=True
        )
        await ctx.respond("✅ Permissions fixed! Bot can now delete messages in this channel.", ephemeral=True)
    except discord.Forbidden:
        await ctx.respond("❌ I don't have permission to modify channel permissions.", ephemeral=True)
    except Exception as e:
        await ctx.respond(f"❌ Error: {str(e)[:100]}", ephemeral=True)


@bot.slash_command(name="mystudio", description="Go to your private studio")
async def mystudio_command(ctx: discord.ApplicationContext):
    """Quick link to user's studio"""
    if not ctx.guild:
        await ctx.respond("This only works in a server!", ephemeral=True)
        return

    channel_id = await get_user_channel(ctx.author.id, ctx.guild.id)
    if channel_id:
        channel = ctx.guild.get_channel(channel_id)
        if channel:
            await ctx.respond(f"Your studio: {channel.mention}", ephemeral=True)
            return

    await ctx.respond(
        "You don't have a studio yet! Use the **Create My Studio** button in the welcome channel.",
        ephemeral=True
    )


# ============== VOICE COMMANDS ==============

@bot.slash_command(name="vjoin", description="Kiara joins your voice channel")
async def vjoin(ctx: discord.ApplicationContext):
    """Make Kiara join the user's voice channel"""
    print(f">>> VJOIN CALLED BY {ctx.author}", flush=True)
    try:
        await ctx.defer()
        print(">>> DEFERRED", flush=True)
    except discord.NotFound:
        print(">>> DEFER FAILED - interaction expired", flush=True)
        return
    except Exception as e:
        print(f">>> DEFER ERROR: {e}", flush=True)
        return

    if not VOICE_AVAILABLE:
        print(">>> VOICE NOT AVAILABLE", flush=True)
        await ctx.followup.send("Voice AI is not available. Missing dependencies.", ephemeral=True)
        return

    if not ctx.author.voice or not ctx.author.voice.channel:
        print(">>> USER NOT IN VOICE", flush=True)
        await ctx.followup.send("You need to be in a voice channel first!", ephemeral=True)
        return

    voice_channel = ctx.author.voice.channel
    print(f">>> TARGET CHANNEL: {voice_channel.name}", flush=True)

    voice_mgr = get_voice_manager()

    if not voice_mgr:
        print(">>> VOICE MANAGER NOT INIT", flush=True)
        await ctx.followup.send("Voice manager not initialized.", ephemeral=True)
        return

    if voice_mgr.is_ptt_enabled():
        voice_mgr.set_ptt_owner(ctx.guild.id, ctx.author.id)

    print(">>> CALLING join_channel...", flush=True)
    try:
        success = await voice_mgr.join_channel(voice_channel)
        print(f">>> join_channel returned: {success}", flush=True)
    except Exception as e:
        print(f">>> EXCEPTION: {e}", flush=True)
        import traceback
        traceback.print_exc()
        await ctx.followup.send(f"Error joining: {e}", ephemeral=True)
        return

    if success:
        embed = discord.Embed(
            title="🎤 Kiara Joined Voice!",
            description=(
                f"I'm now in **{voice_channel.name}**!\n\n"
                "**How to talk to me:**\n"
                "• Say **\"Hey Kiara\"** to start a conversation\n"
                "• Speak naturally - I'll respond in real-time\n"
                "• Say **\"stop\"** or **\"bye\"** when done\n\n"
                "*Or use `/vtalk` to start immediately*"
            ),
            color=BOT_COLOR
        )
        if voice_mgr.is_ptt_enabled():
            embed.add_field(
                name="Push-to-talk",
                value=f"Hold `{VOICE_PTT_KEY}` to speak, release to stop sending audio.",
                inline=False
            )
        await ctx.followup.send(embed=embed)
    else:
        await ctx.followup.send("Failed to join voice channel. Check my permissions!", ephemeral=True)


@bot.slash_command(name="vleave", description="Kiara leaves the voice channel")
async def vleave(ctx: discord.ApplicationContext):
    """Make Kiara leave the voice channel"""
    if not VOICE_AVAILABLE:
        await ctx.respond("Voice AI is not available.", ephemeral=True)
        return

    voice_mgr = get_voice_manager()
    if not voice_mgr:
        await ctx.respond("Voice manager not initialized.", ephemeral=True)
        return

    if not voice_mgr.is_connected(ctx.guild.id):
        await ctx.respond("I'm not in a voice channel!", ephemeral=True)
        return

    await voice_mgr.leave_channel(ctx.guild.id)
    await ctx.respond("👋 Left the voice channel. Talk to you later!", ephemeral=True)


@bot.slash_command(name="vtalk", description="Start talking to Kiara (skip wake word)")
async def vtalk(ctx: discord.ApplicationContext):
    """Manually trigger conversation with Kiara"""
    await ctx.defer()

    if not VOICE_AVAILABLE:
        await ctx.followup.send("Voice AI is not available.", ephemeral=True)
        return

    voice_mgr = get_voice_manager()
    if not voice_mgr:
        await ctx.followup.send("Voice manager not initialized.", ephemeral=True)
        return

    if voice_mgr.is_ptt_enabled():
        voice_mgr.set_ptt_owner(ctx.guild.id, ctx.author.id)

    if not voice_mgr.is_connected(ctx.guild.id):
        if ctx.author.voice and ctx.author.voice.channel:
            success = await voice_mgr.join_channel(ctx.author.voice.channel)
            if not success:
                await ctx.followup.send("Failed to join voice channel!", ephemeral=True)
                return
        else:
            await ctx.followup.send("Join a voice channel first, or use `/vjoin`!", ephemeral=True)
            return

    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.followup.send("You need to be in the voice channel!", ephemeral=True)
        return

    success = await voice_mgr.trigger_wake(ctx.guild.id, ctx.author)

    if success:
        embed = discord.Embed(
            title="🎤 Kiara is Listening!",
            description=(
                f"Go ahead **{ctx.author.display_name}**, I'm all ears!\n\n"
                "• Speak naturally\n"
                "• Say **\"stop\"** or **\"bye\"** when done"
            ),
            color=0x00ff00
        )
        await ctx.followup.send(embed=embed)
    else:
        session = voice_mgr.get_active_session(ctx.guild.id)
        if session:
            await ctx.followup.send(
                f"Please wait - I'm currently talking to **{session.user.display_name}**. "
                "You're in the queue!",
                ephemeral=True
            )
        else:
            await ctx.followup.send("Failed to start conversation. Try again!", ephemeral=True)


@bot.slash_command(name="vstop", description="End your conversation with Kiara")
async def vstop(ctx: discord.ApplicationContext):
    """End the current voice conversation"""
    if not VOICE_AVAILABLE:
        await ctx.respond("Voice AI is not available.", ephemeral=True)
        return

    voice_mgr = get_voice_manager()
    if not voice_mgr:
        await ctx.respond("Voice manager not initialized.", ephemeral=True)
        return

    await voice_mgr.end_session(ctx.author.id)
    await ctx.respond("✅ Conversation ended!", ephemeral=True)


@bot.slash_command(name="vstatus", description="Check voice AI status")
async def vstatus(ctx: discord.ApplicationContext):
    """Show voice AI status"""
    if not VOICE_AVAILABLE:
        await ctx.respond("Voice AI is not available. Missing dependencies.", ephemeral=True)
        return

    voice_mgr = get_voice_manager()
    if not voice_mgr:
        await ctx.respond("Voice manager not initialized.", ephemeral=True)
        return

    connected = voice_mgr.is_connected(ctx.guild.id)
    session = voice_mgr.get_active_session(ctx.guild.id)

    embed = discord.Embed(title="🎤 Voice AI Status", color=BOT_COLOR)
    embed.add_field(
        name="Connection",
        value="🟢 Connected" if connected else "🔴 Not in voice",
        inline=True
    )

    if session:
        embed.add_field(
            name="Active Session",
            value=f"Talking to: **{session.user.display_name}**",
            inline=True
        )
    else:
        embed.add_field(
            name="Active Session",
            value="None - Say 'Hey Kiara' to start!",
            inline=True
        )

    embed.set_footer(text="Use /vjoin to bring Kiara to your channel")
    await ctx.respond(embed=embed)


# ============== STARTUP ==============

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
