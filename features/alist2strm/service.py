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
        self.queued_remote_paths: set[str] = set()
        self.output_candidates: dict[Path, list[AlistEntry]] = {}
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
            "duplicate_groups": 0,
            "duplicate_existing_groups": 0,
            "duplicate_selected_groups": 0,
            "duplicate_source_count": 0,
            "duplicate_ignored_count": 0,
            "duplicate_ignored_bytes": 0,
            "duplicate_details": [],
            "duplicate_details_truncated": 0,
            "invalid_strm_found": 0,
            "invalid_strm_deleted": 0,
            "invalid_sidecars_deleted": 0,
            "invalid_cleanup_errors": [],
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
                    await self._flush_candidates()
                    for _ in range(self.task.max_workers):
                        await self.queue.put(None)

                if self.changed_paths is None and self.task.cleanup_invalid_strm:
                    cleanup = await asyncio.to_thread(self._delete_invalid_strms)
                    self.result["invalid_strm_found"] = cleanup["invalid_strm_found"]
                    self.result["invalid_strm_deleted"] = cleanup["invalid_strm_deleted"]
                    self.result["invalid_sidecars_deleted"] = cleanup[
                        "invalid_sidecars_deleted"
                    ]
                    self.result["invalid_cleanup_errors"] = cleanup["errors"]

        logger_config.logger.info(
            "[alist2strm] task=%s complete created=%s assets=%s skipped=%s failed=%s",
            self.task.uuid,
            self.result["strm_created"],
            self.result["assets_downloaded"],
            self.result["skipped_existing"],
            self.result["failed"],
        )
        if self.result["duplicate_groups"]:
            logger_config.logger.warning(
                "[alist2strm] task=%s resolved duplicate outputs: groups=%s "
                "existing=%s selected_largest=%s ignored_sources=%s",
                self.task.uuid,
                self.result["duplicate_groups"],
                self.result["duplicate_existing_groups"],
                self.result["duplicate_selected_groups"],
                self.result["duplicate_ignored_count"],
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
        local_path = map_local_path(
            self.output_base,
            self.task.source_dir,
            entry.path,
            self.task.flatten_mode,
        )
        self.output_candidates.setdefault(local_path, []).append(entry)

    async def _flush_candidates(self) -> None:
        for local_path, entries in sorted(
            self.output_candidates.items(),
            key=lambda item: str(item[0]),
        ):
            if len(entries) == 1:
                await self.queue.put(entries[0])
                continue

            ranked = sorted(entries, key=lambda entry: (-entry.size, entry.path))
            self.result["duplicate_groups"] += 1
            self.result["duplicate_source_count"] += len(ranked)

            if not self.task.overwrite and await asyncio.to_thread(local_path.exists):
                self.result["duplicate_existing_groups"] += 1
                self.result["duplicate_ignored_count"] += len(ranked)
                self.result["duplicate_ignored_bytes"] += sum(
                    entry.size for entry in ranked
                )
                self.result["skipped_existing"] += len(ranked)
                self._record_duplicate_detail(
                    local_path,
                    action="skipped_existing",
                    selected=None,
                    candidates=ranked,
                )
                continue

            selected = ranked[0]
            ignored = ranked[1:]
            self.result["duplicate_selected_groups"] += 1
            self.result["duplicate_ignored_count"] += len(ignored)
            self.result["duplicate_ignored_bytes"] += sum(
                entry.size for entry in ignored
            )
            self._record_duplicate_detail(
                local_path,
                action=(
                    "overwritten_with_largest"
                    if self.task.overwrite
                    else "selected_largest"
                ),
                selected=selected,
                candidates=ranked,
            )
            await self.queue.put(selected)

    def _record_duplicate_detail(
        self,
        local_path: Path,
        action: str,
        selected: AlistEntry | None,
        candidates: list[AlistEntry],
    ) -> None:
        if len(self.result["duplicate_details"]) >= 100:
            self.result["duplicate_details_truncated"] += 1
            return
        self.result["duplicate_details"].append(
            {
                "output_path": str(local_path),
                "action": action,
                "selected": self._entry_summary(selected) if selected else None,
                "candidates": [self._entry_summary(entry) for entry in candidates],
            }
        )

    @staticmethod
    def _entry_summary(entry: AlistEntry) -> dict:
        return {
            "path": entry.path,
            "size": entry.size,
            "extension": entry.suffix,
        }

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
        if not self.task.overwrite and await asyncio.to_thread(local_path.exists):
            self.result["skipped_existing"] += 1
            return

        if entry.suffix in VIDEO_EXTENSIONS:
            content = await self._strm_content(entry)
            await asyncio.to_thread(self._write_strm_file, local_path, content)
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
        await asyncio.to_thread(self._ensure_output_directory, target.parent)
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
            os.chmod(target, 0o777)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _write_strm_file(self, target: Path, content: str) -> None:
        self._ensure_output_directory(target.parent)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            os.chmod(target, 0o777)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _ensure_output_directory(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)

        current = directory
        while True:
            os.chmod(current, 0o777)
            if current == self.output_base:
                return
            current = current.parent
    async def compare_invalid_strms(self) -> dict:

        """Compare generated STRM files with the current complete AList source tree."""
        async with AsyncAlistClient(
            self.alist_url,
            self.api_key,
            self.request_timeout,
        ) as client:
            self.client = client
            await self._scan_directory(self.task.source_dir)
        records = await asyncio.to_thread(self._invalid_strm_records)
        return {
            "task_uuid": self.task.uuid,
            "source_dir": self.task.source_dir,
            "output_dir": str(self.output_base),
            "source_video_count": sum(
                1
                for entries in self.output_candidates.values()
                for entry in entries
                if entry.suffix in VIDEO_EXTENSIONS
            ),
            "local_strm_count": await asyncio.to_thread(self._local_strm_count),
            "invalid_strm_count": len(records),
            "invalid_strms": records,
        }

    async def delete_invalid_strms(self, selected_paths: list[str]) -> dict:
        """Delete only STRM files still confirmed invalid by a fresh source scan."""
        async with AsyncAlistClient(
            self.alist_url,
            self.api_key,
            self.request_timeout,
        ) as client:
            self.client = client
            await self._scan_directory(self.task.source_dir)
        return await asyncio.to_thread(self._delete_invalid_strms, selected_paths)

    def _valid_strm_paths(self) -> set[Path]:
        return {
            path
            for path, entries in self.output_candidates.items()
            if any(entry.suffix in VIDEO_EXTENSIONS for entry in entries)
        }

    def _local_strm_paths(self) -> list[Path]:
        if not self.output_base.is_dir():
            return []
        return sorted(
            (path for path in self.output_base.rglob("*.strm") if path.is_file()),
            key=str,
        )

    def _local_strm_count(self) -> int:
        return len(self._local_strm_paths())

    def _invalid_strm_records(self) -> list[dict]:
        valid_paths = self._valid_strm_paths()
        return [
            {
                "strm_path": str(path),
                "relative_path": str(path.relative_to(self.output_base)),
                "sidecar_paths": [
                    str(item) for item in self._companion_paths(path, valid_paths)
                ],
            }
            for path in self._local_strm_paths()
            if path not in valid_paths
        ]

    def _companion_paths(self, strm_path: Path, valid_paths: set[Path]) -> list[Path]:
        """Return generated sidecars belonging to a stale STRM without touching peers."""
        stem = strm_path.stem
        sidecar_extensions = (
            SUBTITLE_EXTENSIONS
            | IMAGE_EXTENSIONS
            | NFO_EXTENSIONS
            | self.download_extensions
            | frozenset({".json"})
        )
        valid_stems = {
            path.stem for path in valid_paths if path.parent == strm_path.parent
        }
        companions = []
        for path in strm_path.parent.iterdir():
            if not path.is_file() or path.suffix.lower() not in sidecar_extensions:
                continue
            if not (
                path.stem == stem
                or path.stem.startswith((f"{stem}.", f"{stem}-"))
            ):
                continue
            # A longer, valid STRM name owns its own language sidecar.
            if any(
                path.stem == valid_stem or path.stem.startswith((f"{valid_stem}.", f"{valid_stem}-"))
                for valid_stem in valid_stems
            ):
                continue
            companions.append(path)
        return sorted(companions, key=str)

    def _delete_invalid_strms(self, selected_paths: list[str] | None = None) -> dict:
        records = self._invalid_strm_records()
        selected = None if selected_paths is None else set(selected_paths)
        if selected is not None:
            records = [item for item in records if item["strm_path"] in selected]

        result = {
            "invalid_strm_found": len(records),
            "invalid_strm_deleted": 0,
            "invalid_sidecars_deleted": 0,
            "deleted_strm_paths": [],
            "deleted_sidecar_paths": [],
            "errors": [],
        }
        for record in records:
            paths = [Path(record["strm_path"])] + [
                Path(path) for path in record["sidecar_paths"]
            ]
            for path in paths:
                try:
                    path.unlink()
                    if path.suffix.lower() == ".strm":
                        result["invalid_strm_deleted"] += 1
                        result["deleted_strm_paths"].append(str(path))
                    else:
                        result["invalid_sidecars_deleted"] += 1
                        result["deleted_sidecar_paths"].append(str(path))
                except FileNotFoundError:
                    continue
                except OSError as error:
                    result["errors"].append({"path": str(path), "error": str(error)})
        return result



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
