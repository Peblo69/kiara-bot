import aiosqlite
from datetime import datetime, timezone
from typing import Optional
import json

DATABASE_PATH = "nano_midjourney.db"


async def init_db():
    """Initialize database tables"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        # Users table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_generations INTEGER DEFAULT 0
            )
        """)

        # Daily usage tracking
        await db.execute("""
            CREATE TABLE IF NOT EXISTS daily_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                date TEXT,
                count INTEGER DEFAULT 0,
                UNIQUE(user_id, date)
            )
        """)

        # Generation history (stores image data for variations/upscale)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS generations (
                id TEXT PRIMARY KEY,
                user_id INTEGER,
                prompt TEXT,
                images_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                message_id INTEGER,
                channel_id INTEGER
            )
        """)

        # User reference images (up to 5 per user)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_references (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                slot INTEGER,
                image_data TEXT,
                mime_type TEXT,
                filename TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, slot)
            )
        """)

        # User settings
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                model TEXT DEFAULT 'gemini-3-pro-image-preview',
                quality TEXT DEFAULT '1K',
                aspect_ratio TEXT DEFAULT '1:1',
                style TEXT DEFAULT 'none',
                panel_channel_id INTEGER,
                panel_message_id INTEGER
            )
        """)

        # Add style column if missing (migration)
        try:
            await db.execute("ALTER TABLE user_settings ADD COLUMN style TEXT DEFAULT 'none'")
        except:
            pass

        # User private channels
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                guild_id INTEGER,
                channel_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, guild_id)
            )
        """)

        await db.commit()


async def get_or_create_user(user_id: int, username: str) -> dict:
    """Get or create user record"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        )
        user = await cursor.fetchone()

        if not user:
            await db.execute(
                "INSERT INTO users (user_id, username) VALUES (?, ?)",
                (user_id, username)
            )
            await db.commit()
            return {"user_id": user_id, "username": username, "total_generations": 0}

        return dict(user)


async def get_daily_usage(user_id: int) -> int:
    """Get today's generation count for user"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT count FROM daily_usage WHERE user_id = ? AND date = ?",
            (user_id, today)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def increment_usage(user_id: int, amount: int = 1) -> int:
    """Increment usage counter, return new total for today"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async with aiosqlite.connect(DATABASE_PATH) as db:
        # Upsert daily usage
        await db.execute(f"""
            INSERT INTO daily_usage (user_id, date, count) VALUES (?, ?, ?)
            ON CONFLICT(user_id, date) DO UPDATE SET count = count + ?
        """, (user_id, today, amount, amount))

        # Update total counter
        await db.execute(
            "UPDATE users SET total_generations = total_generations + ? WHERE user_id = ?",
            (amount, user_id)
        )

        await db.commit()

        cursor = await db.execute(
            "SELECT count FROM daily_usage WHERE user_id = ? AND date = ?",
            (user_id, today)
        )
        row = await cursor.fetchone()
        return row[0]


async def save_generation(gen_id: str, user_id: int, prompt: str,
                          images_data: list, message_id: int, channel_id: int):
    """Save generation for later upscale/variations"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO generations
               (id, user_id, prompt, images_data, message_id, channel_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (gen_id, user_id, prompt, json.dumps(images_data), message_id, channel_id)
        )
        await db.commit()


async def get_generation(gen_id: str) -> Optional[dict]:
    """Retrieve generation data by ID"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM generations WHERE id = ?", (gen_id,)
        )
        row = await cursor.fetchone()
        if row:
            data = dict(row)
            data["images_data"] = json.loads(data["images_data"])
            return data
        return None


# ============== USER SETTINGS ==============

async def get_user_settings(user_id: int) -> dict:
    """Get user settings, create defaults if not exist"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM user_settings WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()

        if not row:
            await db.execute(
                "INSERT INTO user_settings (user_id) VALUES (?)", (user_id,)
            )
            await db.commit()
            return {
                "user_id": user_id,
                "model": "gemini-3-pro-image-preview",
                "quality": "1K",
                "aspect_ratio": "1:1",
                "style": "none",
                "panel_channel_id": None,
                "panel_message_id": None
            }

        return dict(row)


async def update_user_settings(user_id: int, **kwargs) -> None:
    """Update user settings"""
    allowed = ["model", "quality", "aspect_ratio", "panel_channel_id", "panel_message_id", "style"]
    updates = {k: v for k, v in kwargs.items() if k in allowed}

    if not updates:
        return

    set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
    values = list(updates.values()) + [user_id]

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            f"INSERT INTO user_settings (user_id) VALUES (?) ON CONFLICT(user_id) DO NOTHING",
            (user_id,)
        )
        await db.execute(
            f"UPDATE user_settings SET {set_clause} WHERE user_id = ?",
            values
        )
        await db.commit()


# ============== REFERENCE IMAGES ==============

async def save_reference_image(user_id: int, slot: int, image_data: str,
                                mime_type: str, filename: str) -> None:
    """Save a reference image to a slot (1-5)"""
    if slot < 1 or slot > 5:
        raise ValueError("Slot must be 1-5")

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO user_references (user_id, slot, image_data, mime_type, filename)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, slot) DO UPDATE SET
                image_data = excluded.image_data,
                mime_type = excluded.mime_type,
                filename = excluded.filename,
                created_at = CURRENT_TIMESTAMP
        """, (user_id, slot, image_data, mime_type, filename))
        await db.commit()


async def get_reference_images(user_id: int) -> list[dict]:
    """Get all reference images for a user"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM user_references WHERE user_id = ? ORDER BY slot",
            (user_id,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_reference_image(user_id: int, slot: int) -> Optional[dict]:
    """Get a specific reference image"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM user_references WHERE user_id = ? AND slot = ?",
            (user_id, slot)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def delete_reference_image(user_id: int, slot: int) -> bool:
    """Delete a reference image from a slot"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM user_references WHERE user_id = ? AND slot = ?",
            (user_id, slot)
        )
        await db.commit()
        return cursor.rowcount > 0


async def clear_all_references(user_id: int) -> int:
    """Clear all reference images for a user"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM user_references WHERE user_id = ?", (user_id,)
        )
        await db.commit()
        return cursor.rowcount


# ============== USER PRIVATE CHANNELS ==============

async def save_user_channel(user_id: int, channel_id: int, guild_id: int) -> None:
    """Save user's private channel ID"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO user_channels (user_id, channel_id, guild_id)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, guild_id) DO UPDATE SET
                channel_id = excluded.channel_id
        """, (user_id, channel_id, guild_id))
        await db.commit()


async def get_user_channel(user_id: int, guild_id: int) -> Optional[int]:
    """Get user's private channel ID for a guild"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT channel_id FROM user_channels WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id)
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def delete_user_channel(user_id: int, guild_id: int) -> bool:
    """Delete user's channel record"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM user_channels WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id)
        )
        await db.commit()
        return cursor.rowcount > 0
