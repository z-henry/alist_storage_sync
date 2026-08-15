import asyncio
import os
from pathlib import Path
from uuid import uuid4

from aiohttp import ClientSession, ClientTimeout

import logger_config

from .client import AlistEntry, AsyncAlistClient
from .models import Alist2StrmTask
from .path_mapper import (
    IMAGE_EXTENSIONS,
    NFO_EXTENSIONS,
    SUBTITLE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    map_local_path,
    resolve_output_base,
)


class Alist2StrmService:
    def __init__(
        self,
        task: Alist2StrmTask,
        alist_url: str,
        api_key: str,
        request_timeout: float,
        output_root: str,
        changed_paths: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self.task = task
        self.changed_paths = (
            None if changed_paths is None else tuple(dict.fromkeys(changed_paths))
        )
        self.alist_url = alist_url
        self.api_key = api_key
        self.request_timeout = request_timeout
        self.output_base = resolve_output_base(output_root, task.target_dir)
        self.queue: asyncio.Queue[AlistEntry | None] = asyncio.Queue(
            maxsize=max(10, task.max_workers * 4)
        )
        self.download_slots = asyncio.Semaphore(task.max_downloaders)
        self.claimed_paths: set[Path] = set()
        self.queued_remote_paths: set[str] = set()
        self.result = {
            "scanned": 0,
            "strm_created": 0,
            "assets_downloaded": 0,
            "skipped_existing": 0,
            "unsupported": 0,
            "failed": 0,
            "errors": [],
            "source_dir": task.source_dir,
            "output_dir": str(self.output_base),
            "incremental": self.changed_paths is not None,
            "requested_path_count": len(self.changed_paths or ()),
        }

        download_extensions = set(task.other_extensions)
        if task.subtitle and not task.flatten_mode:
            download_extensions.update(SUBTITLE_EXTENSIONS)
        if task.image and not task.flatten_mode:
            download_extensions.update(IMAGE_EXTENSIONS)
        if task.nfo and not task.flatten_mode:
            download_extensions.update(NFO_EXTENSIONS)
        self.download_extensions = frozenset(download_extensions)
        self.process_extensions = VIDEO_EXTENSIONS | self.download_extensions

    async def run(self) -> dict:
        logger_config.logger.info(
            "[alist2strm] task=%s scan=%s output=%s",
            self.task.uuid,
            self.task.source_dir,
            self.output_base,
        )
        async with AsyncAlistClient(
            self.alist_url,
            self.api_key,
            self.request_timeout,
        ) as client:
            self.client = client
            download_timeout = ClientTimeout(
                total=None,
                sock_connect=self.request_timeout,
                sock_read=self.request_timeout,
            )
            async with ClientSession(timeout=download_timeout) as download_session:
                self.download_session = download_session
                async with asyncio.TaskGroup() as group:
                    for worker_number in range(self.task.max_workers):
                        group.create_task(self._worker(worker_number))
                    if self.changed_paths is None:
                        await self._scan_directory(self.task.source_dir)
                    else:
                        await self._scan_changed_paths()
                    for _ in range(self.task.max_workers):
                        await self.queue.put(None)

        logger_config.logger.info(
            "[alist2strm] task=%s complete created=%s assets=%s skipped=%s failed=%s",
            self.task.uuid,
            self.result["strm_created"],
            self.result["assets_downloaded"],
            self.result["skipped_existing"],
            self.result["failed"],
        )
        return self.result

    async def _scan_directory(self, directory: str) -> None:
        for entry in await self.client.list_dir(directory):
            if entry.is_dir:
                await self._scan_directory(entry.path)
                continue
            await self._queue_entry(entry)

    async def _scan_changed_paths(self) -> None:
        for path in self.changed_paths or ():
            try:
                entry = await self.client.get_path(path)
                if entry.is_dir:
                    await self._scan_directory(entry.path)
                else:
                    await self._queue_entry(entry)
            except Exception as error:
                self.result["failed"] += 1
                if len(self.result["errors"]) < 100:
                    self.result["errors"].append({"path": path, "error": str(error)})
                logger_config.logger.exception(
                    "[alist2strm] incremental path=%s failed: %s",
                    path,
                    error,
                )

    async def _queue_entry(self, entry: AlistEntry) -> None:
        if entry.path in self.queued_remote_paths:
            return
        self.queued_remote_paths.add(entry.path)
        self.result["scanned"] += 1
        if entry.suffix not in self.process_extensions:
            self.result["unsupported"] += 1
            return
        await self.queue.put(entry)

    async def _worker(self, worker_number: int) -> None:
        while True:
            entry = await self.queue.get()
            try:
                if entry is None:
                    return
                await self._process_entry(entry)
            except Exception as error:
                self.result["failed"] += 1
                if len(self.result["errors"]) < 100:
                    self.result["errors"].append(
                        {"path": getattr(entry, "path", None), "error": str(error)}
                    )
                logger_config.logger.exception(
                    "[alist2strm] worker=%s path=%s failed: %s",
                    worker_number,
                    getattr(entry, "path", None),
                    error,
                )
            finally:
                self.queue.task_done()

    async def _process_entry(self, entry: AlistEntry) -> None:
        local_path = map_local_path(
            self.output_base,
            self.task.source_dir,
            entry.path,
            self.task.flatten_mode,
        )
        if local_path in self.claimed_paths:
            raise RuntimeError(f"Multiple source files map to the same output: {local_path}")
        self.claimed_paths.add(local_path)

        if not self.task.overwrite and await asyncio.to_thread(local_path.exists):
            self.result["skipped_existing"] += 1
            return

        if entry.suffix in VIDEO_EXTENSIONS:
            content = await self._strm_content(entry)
            await asyncio.to_thread(self._atomic_write_text, local_path, content)
            self.result["strm_created"] += 1
            return

        async with self.download_slots:
            await self._download(entry.download_url, local_path)
        self.result["assets_downloaded"] += 1

    async def _strm_content(self, entry: AlistEntry) -> str:
        if self.task.mode == "alist_url":
            return entry.download_url
        if self.task.mode == "alist_path":
            return entry.path
        if self.task.mode == "raw_url":
            detail = entry if entry.raw_url else await self.client.get_entry(entry)
            if not detail.raw_url:
                raise RuntimeError(f"AList did not return raw_url for {entry.path}")
            return detail.raw_url
        raise RuntimeError(f"Unsupported Alist2Strm mode: {self.task.mode}")

    async def _download(self, url: str, target: Path) -> None:
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            async with self.download_session.get(url) as response:
                response.raise_for_status()
                with temporary.open("wb") as output:
                    async for chunk in response.content.iter_chunked(1024 * 256):
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _atomic_write_text(target: Path, content: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()


def run_alist2strm(
    task: Alist2StrmTask,
    alist_url: str,
    api_key: str,
    request_timeout: float,
    output_root: str,
    changed_paths: list[str] | tuple[str, ...] | None = None,
) -> dict:
    return asyncio.run(
        Alist2StrmService(
            task,
            alist_url,
            api_key,
            request_timeout,
            output_root,
            changed_paths,
        ).run()
    )
