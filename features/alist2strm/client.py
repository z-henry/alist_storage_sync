from pathlib import PurePosixPath

from aiohttp import ClientSession, ClientTimeout

from .models import AlistEntry


class AsyncAlistClient:
    def __init__(self, url: str, api_key: str, timeout_seconds: float) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = ClientTimeout(total=timeout_seconds)
        self.session: ClientSession | None = None
        self.base_path = "/"

    async def __aenter__(self):
        self.session = ClientSession(
            headers={
                "Authorization": self.api_key,
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )
        try:
            me = await self._request("GET", "/api/me")
            self.base_path = (me or {}).get("base_path") or "/"
            return self
        except Exception:
            await self.session.close()
            self.session = None
            raise

    async def __aexit__(self, *_):
        if self.session:
            await self.session.close()

    async def _request(self, method: str, endpoint: str, **kwargs):
        if not self.session:
            raise RuntimeError("AList client is not open")
        async with self.session.request(method, self.url + endpoint, **kwargs) as response:
            response.raise_for_status()
            payload = await response.json()
        if payload.get("code") != 200:
            raise RuntimeError(
                f"AList {endpoint} failed: {payload.get('message') or 'unknown error'}"
            )
        return payload.get("data")

    async def list_dir(self, directory: str) -> list[AlistEntry]:
        directory = "/" + directory.strip("/")
        data = await self._request(
            "POST",
            "/api/fs/list",
            json={
                "path": directory,
                "password": "",
                "page": 1,
                "per_page": 0,
                "refresh": False,
            },
        )
        content = (data or {}).get("content") or []
        return [self._entry(directory, item) for item in content]

    async def get_path(self, path: str) -> AlistEntry:
        normalized_path = "/" + path.strip("/")
        data = await self._request(
            "POST",
            "/api/fs/get",
            json={"path": normalized_path, "password": ""},
        )
        return self._entry(str(PurePosixPath(normalized_path).parent), data or {})

    async def get_entry(self, entry: AlistEntry) -> AlistEntry:
        return await self.get_path(entry.path)

    def _entry(self, directory: str, item: dict) -> AlistEntry:
        name = str(item.get("name") or "")
        if not name:
            raise RuntimeError("AList returned an entry without a name")
        path = str(PurePosixPath(directory) / name)
        return AlistEntry(
            server_url=self.url,
            base_path=self.base_path,
            path=path,
            name=name,
            size=int(item.get("size") or 0),
            is_dir=bool(item.get("is_dir")),
            sign=str(item.get("sign") or ""),
            raw_url=item.get("raw_url"),
        )
