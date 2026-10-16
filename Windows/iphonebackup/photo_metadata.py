"""Complete-file previews and explicitly sourced capture dates."""
from datetime import datetime
from io import BytesIO
from pathlib import Path
import sys
import tempfile
from threading import Event
import xml.etree.ElementTree as ET

from .models import DeviceFile

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".gif", ".tif", ".tiff", ".dng"}


def parse_date(value) -> datetime | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        return None
    value = value.strip(" \x00")
    if not value or value.startswith("0000"):
        return None
    for pattern in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            pass
    try:
        # Keep the recorded wall-clock date/month; do not convert it to the
        # computer's timezone or mix aware and naive dates during sorting.
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def exif_date(exif) -> datetime | None:
    candidates = [exif.get(36867)]
    try:
        candidates.insert(0, exif.get_ifd(34665).get(36867))
    except (AttributeError, KeyError, ValueError, OSError, TypeError):
        pass
    return next((date for value in candidates if (date := parse_date(value))), None)


def xmp_date(packet) -> datetime | None:
    if not isinstance(packet, (str, bytes)) or len(packet) > 2 * 1024 * 1024:
        return None
    try:
        tree = ET.fromstring(packet)
    except (ET.ParseError, ValueError):
        return None
    # Only capture/creation fields, never MetadataDate or ModifyDate.
    keys = ("{http://ns.adobe.com/exif/1.0/}DateTimeOriginal", "{http://ns.adobe.com/xap/1.0/}CreateDate",
            "{http://ns.adobe.com/photoshop/1.0/}DateCreated")
    for key in keys:
        for element in tree.iter():
            date = parse_date(element.attrib.get(key))
            if date:
                return date
            if element.tag == key and (date := parse_date(element.text)):
                return date
    return None


def inspect_pillow(path: Path):
    from PIL import Image, ImageOps
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass
    try:
        opened = Image.open(path)
    except Image.DecompressionBombError as error:
        raise ValueError("圖片像素超過安全解碼範圍。") from error
    with opened as image:
        captured = exif_date(image.getexif())
        source = "EXIF 拍攝日期" if captured else None
        if captured is None:
            captured = xmp_date(image.info.get("xmp") or image.info.get("XML:com.adobe.xmp"))
            source = "XMP 拍攝／建立日期" if captured else None
        try:
            # thumbnail() may use JPEG/HEIF decoder downsampling. Orient the
            # small result rather than copying full-size 48 MP images.
            image.thumbnail((320, 320))
            thumbnail = ImageOps.exif_transpose(image)
            buffer = BytesIO()
            if thumbnail.mode in ("RGBA", "LA") or (thumbnail.mode == "P" and "transparency" in thumbnail.info):
                rgba = thumbnail.convert("RGBA")
                rgb = Image.new("RGB", rgba.size, "white")
                rgb.paste(rgba, mask=rgba.getchannel("A"))
            else:
                rgb = thumbnail.convert("RGB")
            rgb.save(buffer, "JPEG", quality=85)
            return captured, source, buffer.getvalue()
        except (OSError, ValueError, SyntaxError):
            # Pixel decode failure must not discard a successfully read date.
            return captured, source, None


def inspect_photo(session, file: DeviceFile, cancel: Event):
    """Return (capture date, date source, JPEG thumbnail, preview error).

    Full media is streamed into one private temporary file, never concatenated
    in RAM. It is removed after decoding; originals are not rewritten.
    """
    if Path(file.path).suffix.lower() not in IMAGE_EXTENSIONS:
        return None, None, None, None
    # A real suffix lets system decoders recognize the format. delete_on_close
    # is not used, so this also works with Windows libraries opening the file.
    fd, name = tempfile.mkstemp(prefix="iphonebackup-preview-", suffix=Path(file.path).suffix)
    path = Path(name)
    captured, source, preview = None, None, None
    try:
        import os
        with os.fdopen(fd, "wb") as output:
            count = 0
            for chunk in session.chunks(file):
                if cancel.is_set():
                    raise RuntimeError("已取消讀取照片預覽。")
                count += len(chunk)
                if count > file.size:
                    raise RuntimeError("預覽來源容量已改變，請重新讀取清單。")
                output.write(chunk)
            if count != file.size:
                raise RuntimeError("預覽來源讀取不完整，請重新讀取清單。")
        if cancel.is_set():
            raise RuntimeError("已取消讀取照片預覽。")
        if sys.platform == "darwin":
            try:
                from .imageio_macos import inspect_image
                metadata, preview = inspect_image(path)
                captured = parse_date(metadata.get("{Exif}", {}).get("DateTimeOriginal"))
                source = "EXIF 拍攝日期" if captured else None
            except (OSError, ValueError, KeyError, TypeError):
                pass
        if preview is None or captured is None:
            try:
                date, date_source, pillow_preview = inspect_pillow(path)
                if captured is None:
                    captured, source = date, date_source
                preview = preview or pillow_preview
            except (OSError, ValueError, SyntaxError):
                pass
        error = None if preview else "無法解碼這張照片的預覽；原檔仍可選取匯出。"
        if error and sys.platform != "darwin" and path.suffix.lower() in {".heic", ".heif"}:
            error += "請確認已安裝 pillow-heif。"
        return captured, source, preview, error
    finally:
        path.unlink(missing_ok=True)
