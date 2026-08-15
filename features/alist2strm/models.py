from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote, urlencode



@dataclass(frozen=True)
class AlistEntry:
    server_url: str
    base_path: str
    path: str
    name: str
    size: int
    is_dir: bool
    sign: str = ""
    raw_url: str | None = None

    @property
    def suffix(self) -> str:
        return "" if self.is_dir else PurePosixPath(self.name).suffix.lower()

    @property
    def download_url(self) -> str:
        full_path = (
            "/"
            + self.base_path.strip("/")
            + "/"
            + self.path.strip("/")
        ).replace("//", "/")
        url = f"{self.server_url}/d{quote(full_path, safe='/')}"
        if self.sign:
            url += "?" + urlencode({"sign": self.sign})
        return url


@dataclass(frozen=True)
class Alist2StrmTask:
    uuid: str
    source_dir: str
    target_dir: str
    cron: str = "0 */6 * * *"
    flatten_mode: bool = False
    subtitle: bool = False
    image: bool = False
    nfo: bool = False
    mode: str = "alist_url"
    overwrite: bool = False
    other_extensions: tuple[str, ...] = ()
    max_workers: int = 20
    max_downloaders: int = 3

    def parameters(self) -> dict:
        return {
            "source_dir": self.source_dir,
            "target_dir": self.target_dir,
            "flatten_mode": self.flatten_mode,
            "subtitle": self.subtitle,
            "image": self.image,
            "nfo": self.nfo,
            "mode": self.mode,
            "overwrite": self.overwrite,
            "other_extensions": list(self.other_extensions),
            "max_workers": self.max_workers,
            "max_downloaders": self.max_downloaders,
        }
