from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DeviceFile:
    path: str
    size: int
    modified_ns: str | None = None
    source: str = "media"


@dataclass(frozen=True)
class MediaItem:
    id: str
    title: str
    category: str
    files: tuple[DeviceFile, ...]
    captured: datetime | None = None
    thumbnail: bytes | None = None
    date_source: str | None = None
    preview_error: str | None = None
    duration_seconds: float | None = None
    recording_metadata: dict | None = None

    @property
    def size(self) -> int:
        return sum(file.size for file in self.files)

    @property
    def month(self) -> str:
        return self.captured.strftime("%Y-%m") if self.captured else "日期未知"


@dataclass(frozen=True)
class Catalog:
    items: tuple[MediaItem, ...]
    warnings: tuple[str, ...]
    # USB file access alone cannot establish Photos/Voice Memos library coverage.
    complete: bool = False
    availability: str = "readable"


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")
