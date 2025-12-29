from PIL import Image
import io
from typing import List


def create_image_grid(images: List[bytes], grid_size: int = 2) -> bytes:
    """
    Create a 2x2 grid from 4 images (Midjourney style)

    Args:
        images: List of PNG image bytes (4 images)
        grid_size: Grid dimension (2 = 2x2 grid)

    Returns:
        PNG bytes of combined grid image
    """
    if len(images) != grid_size * grid_size:
        raise ValueError(f"Expected {grid_size * grid_size} images, got {len(images)}")

    # Load images
    pil_images = [Image.open(io.BytesIO(img_bytes)) for img_bytes in images]

    # Get dimensions (assume all same size)
    img_width, img_height = pil_images[0].size

    # Add small gap between images
    gap = 4

    # Create canvas
    grid_width = (img_width * grid_size) + (gap * (grid_size - 1))
    grid_height = (img_height * grid_size) + (gap * (grid_size - 1))
    grid_image = Image.new("RGB", (grid_width, grid_height), color=(54, 57, 63))  # Discord dark bg

    # Place images in grid
    for idx, img in enumerate(pil_images):
        row = idx // grid_size
        col = idx % grid_size
        x = col * (img_width + gap)
        y = row * (img_height + gap)
        grid_image.paste(img, (x, y))

    # Save to bytes
    output = io.BytesIO()
    grid_image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output.getvalue()


def extract_image_from_grid(grid_bytes: bytes, index: int, grid_size: int = 2) -> bytes:
    """
    Extract a single image from the grid by index (0-3)

    Args:
        grid_bytes: The grid image bytes
        index: Which image to extract (0=top-left, 1=top-right, 2=bottom-left, 3=bottom-right)
        grid_size: Grid dimension

    Returns:
        PNG bytes of extracted image
    """
    grid_image = Image.open(io.BytesIO(grid_bytes))
    grid_width, grid_height = grid_image.size

    gap = 4
    img_width = (grid_width - (gap * (grid_size - 1))) // grid_size
    img_height = (grid_height - (gap * (grid_size - 1))) // grid_size

    row = index // grid_size
    col = index % grid_size
    x = col * (img_width + gap)
    y = row * (img_height + gap)

    # Crop the image
    cropped = grid_image.crop((x, y, x + img_width, y + img_height))

    # Save to bytes
    output = io.BytesIO()
    cropped.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output.getvalue()


def upscale_image(image_bytes: bytes, scale: float = 2.0) -> bytes:
    """
    Upscale an image using high-quality resampling

    Args:
        image_bytes: Original image bytes
        scale: Scale factor (2.0 = double size)

    Returns:
        PNG bytes of upscaled image
    """
    img = Image.open(io.BytesIO(image_bytes))
    new_width = int(img.width * scale)
    new_height = int(img.height * scale)

    # Use LANCZOS for high quality upscaling
    upscaled = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

    output = io.BytesIO()
    upscaled.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output.getvalue()
