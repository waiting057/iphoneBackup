"""macOS system ImageIO decoding. No compiler or extra HEIC package required."""
import ctypes as c
import os
import plistlib
from pathlib import Path


def inspect_image(path: Path) -> tuple[dict, bytes | None]:
    cf = c.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    io = c.CDLL("/System/Library/Frameworks/ImageIO.framework/ImageIO")
    cg = c.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    p = c.c_void_p

    def bind(lib, name, args, result=p):
        fn = getattr(lib, name)
        fn.argtypes, fn.restype = args, result
        return fn

    release = bind(cf, "CFRelease", [p], None)
    url_create = bind(cf, "CFURLCreateFromFileSystemRepresentation", [p, c.c_char_p, c.c_long, c.c_bool])
    source_create = bind(io, "CGImageSourceCreateWithURL", [p, p])
    properties_copy = bind(io, "CGImageSourceCopyPropertiesAtIndex", [p, c.c_size_t, p])
    plist_data = bind(cf, "CFPropertyListCreateData", [p, p, c.c_long, c.c_ulong, p])
    data_length = bind(cf, "CFDataGetLength", [p], c.c_long)
    data_bytes = bind(cf, "CFDataGetBytePtr", [p])
    dictionary_create = bind(cf, "CFDictionaryCreate", [p, c.POINTER(p), c.POINTER(p), c.c_long, p, p])
    number_create = bind(cf, "CFNumberCreate", [p, c.c_int, p])
    thumbnail_create = bind(io, "CGImageSourceCreateThumbnailAtIndex", [p, c.c_size_t, p])
    image_release = bind(cg, "CGImageRelease", [p], None)
    data_create = bind(cf, "CFDataCreateMutable", [p, c.c_long])
    string_create = bind(cf, "CFStringCreateWithCString", [p, c.c_char_p, c.c_uint])
    destination_create = bind(io, "CGImageDestinationCreateWithData", [p, p, c.c_size_t, p])
    add_image = bind(io, "CGImageDestinationAddImage", [p, p, p], None)
    finalize = bind(io, "CGImageDestinationFinalize", [p], c.c_bool)
    owned = []
    image = None

    def own(value):
        if value:
            owned.append(value)
        return value

    def bytes_from(data):
        return c.string_at(data_bytes(data), data_length(data))

    try:
        encoded = os.fsencode(path)
        url = own(url_create(None, encoded, len(encoded), False))
        source = own(source_create(url, None)) if url else None
        if not source:
            raise ValueError("系統無法辨識這個圖片格式。")
        properties = own(properties_copy(source, 0, None))
        serialized = own(plist_data(None, properties, 100, 0, None)) if properties else None
        metadata = plistlib.loads(bytes_from(serialized)) if serialized else {}
        maximum = c.c_int(320)
        number = own(number_create(None, 9, c.byref(maximum)))  # kCFNumberIntType
        true = p.in_dll(cf, "kCFBooleanTrue").value
        keys = (p * 3)(*[p.in_dll(io, name).value for name in (
            "kCGImageSourceCreateThumbnailFromImageAlways",
            "kCGImageSourceCreateThumbnailWithTransform",
            "kCGImageSourceThumbnailMaxPixelSize")])
        values = (p * 3)(true, true, number)
        # All entries outlive this non-retaining temporary dictionary.
        options = own(dictionary_create(None, keys, values, 3, None, None))
        image = thumbnail_create(source, 0, options)
        if not image:
            return metadata, None
        output = own(data_create(None, 0))
        jpeg_type = own(string_create(None, b"public.jpeg", 0x08000100))
        destination = own(destination_create(output, jpeg_type, 1, None))
        if not destination:
            return metadata, None
        add_image(destination, image, None)
        return metadata, bytes_from(output) if finalize(destination) else None
    finally:
        if image:
            image_release(image)
        for value in reversed(owned):
            release(value)
