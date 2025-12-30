from google import genai
from google.genai import types
import asyncio
import base64
import os

# Get API key directly from environment (more reliable in containers)
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Initialize client (only if API key exists)
client = None
if GOOGLE_API_KEY:
    client = genai.Client(api_key=GOOGLE_API_KEY)
else:
    print("[WARNING] GOOGLE_API_KEY not set - image generation disabled")

# Models - Gemini 3 Pro Image works globally
MODELS = {
    "gemini-3-pro-image-preview": "gemini-3-pro-image-preview",
    "gemini-2.5-flash-image": "gemini-2.5-flash-image",
    "gemini-2.5-flash-image-preview": "gemini-2.5-flash-image-preview",
}
DEFAULT_MODEL = "gemini-3-pro-image-preview"


class ImagenGenerator:
    """Image generation using Gemini image models"""

    def __init__(self):
        pass

    async def generate_with_refs(
        self,
        prompt: str,
        reference_images: list[dict] = None,
        aspect_ratio: str = "1:1",
        quality: str = "1K",
        model: str = None
    ) -> bytes:
        """
        Generate image with optional reference images

        Args:
            prompt: Text description
            reference_images: List of {"base64": str, "mimeType": str}
            aspect_ratio: Aspect ratio
            quality: 1K, 2K, 4K (maps to image size config)
            model: Model to use

        Returns:
            Image bytes
        """
        use_model = model if model in MODELS else DEFAULT_MODEL
        refs = reference_images or []

        max_retries = 3
        base_delay = 2.0

        for attempt in range(max_retries):
            try:
                loop = asyncio.get_event_loop()

                # Build parts: reference images first, then prompt
                parts = []

                # Add reference images
                for ref in refs:
                    parts.append({
                        "inline_data": {
                            "mime_type": ref["mimeType"],
                            "data": ref["base64"],
                        }
                    })

                # Build instruction based on refs
                if len(refs) >= 2:
                    instruction = (
                        "IMPORTANT: Use the face/person from the FIRST reference image(s). "
                        "The LAST image shows the pose/scene/style to recreate. "
                        "Keep the face IDENTICAL but recreate the pose and setting. "
                    )
                elif len(refs) == 1:
                    instruction = (
                        "Use the exact face/style from the reference image. "
                        "Keep it identical while following this description: "
                    )
                else:
                    instruction = ""

                parts.append({"text": instruction + prompt})

                # Config - IMAGE only, no text
                config = types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                )

                # Generate
                result = await loop.run_in_executor(
                    None,
                    lambda: client.models.generate_content(
                        model=use_model,
                        contents={"parts": parts},
                        config=config,
                    )
                )

                # Extract image
                if result.candidates and len(result.candidates) > 0:
                    candidate = result.candidates[0]
                    if candidate.content and candidate.content.parts:
                        for part in candidate.content.parts:
                            if hasattr(part, 'inline_data') and part.inline_data:
                                data = part.inline_data.data
                                # Data is already bytes, no need to decode
                                if isinstance(data, bytes):
                                    img_bytes = data
                                else:
                                    # Fallback if it's base64 string
                                    img_bytes = base64.b64decode(data)
                                print(f"[Gemini] Generated with {use_model}, {len(refs)} refs, size: {len(img_bytes)}")
                                return img_bytes

                raise Exception("No image in response")

            except Exception as e:
                error_msg = str(e).lower()
                print(f"[Gemini] Error: {e}")

                if "429" in error_msg or "rate" in error_msg or "quota" in error_msg:
                    delay = base_delay * (2 ** attempt)
                    print(f"Rate limit, waiting {delay}s...")
                    await asyncio.sleep(delay)
                elif "blocked" in error_msg or "safety" in error_msg:
                    raise ValueError("Prompt blocked by safety filter - try different wording")
                else:
                    if attempt == max_retries - 1:
                        raise
                    await asyncio.sleep(base_delay)

        raise Exception("Max retries exceeded")

    async def generate_single(
        self,
        prompt: str,
        aspect_ratio: str = "1:1"
    ) -> bytes:
        """Generate a single image"""
        return await self.generate_with_refs(prompt=prompt, aspect_ratio=aspect_ratio)


# Global instance
imagen = ImagenGenerator()
