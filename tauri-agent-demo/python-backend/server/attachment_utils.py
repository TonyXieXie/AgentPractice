from io import BytesIO
from typing import Optional, Tuple

from PIL import Image


def build_thumbnail(data: bytes, max_size: int = 360) -> Optional[Tuple[str, bytes]]:
    try:
        with Image.open(BytesIO(data)) as img:
            img.load()
            img.thumbnail((max_size, max_size))
            has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
            output = BytesIO()
            if has_alpha:
                if img.mode not in ("RGBA", "LA"):
                    img = img.convert("RGBA")
                img.save(output, format="PNG")
                return "image/png", output.getvalue()
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(output, format="JPEG", quality=85)
            return "image/jpeg", output.getvalue()
    except Exception:
        return None

