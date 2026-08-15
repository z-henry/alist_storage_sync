import tempfile
import unittest
from pathlib import Path

from aiohttp import web

from features.alist2strm.models import Alist2StrmTask
from features.alist2strm.service import Alist2StrmService


class Alist2StrmServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        application = web.Application()
        application.router.add_get("/api/me", self._me)
        application.router.add_post("/api/fs/list", self._list)
        application.router.add_post("/api/fs/get", self._get)
        application.router.add_get("/d/{path:.*}", self._download)
        self.get_paths = []
        self.runner = web.AppRunner(application)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.alist_url = f"http://127.0.0.1:{port}"

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def _me(self, request):
        self.assertEqual(request.headers.get("Authorization"), "test-key")
        return web.json_response({"code": 200, "data": {"base_path": "/"}})

    async def _list(self, request):
        self.assertEqual(request.headers.get("Authorization"), "test-key")
        payload = await request.json()
        if payload["path"] == "/source":
            content = [
                {
                    "name": "movie.mkv",
                    "size": 100,
                    "is_dir": False,
                    "sign": "video-sign",
                },
                {
                    "name": "poster.jpg",
                    "size": 5,
                    "is_dir": False,
                    "sign": "image-sign",
                },
            ]
        else:
            content = []
        return web.json_response({"code": 200, "data": {"content": content}})

    async def _get(self, request):
        self.assertEqual(request.headers.get("Authorization"), "test-key")
        payload = await request.json()
        self.get_paths.append(payload["path"])
        entries = {
            "/source/movie.mkv": {
                "name": "movie.mkv",
                "size": 100,
                "is_dir": False,
                "sign": "video-sign",
            },
            "/source": {
                "name": "source",
                "size": 0,
                "is_dir": True,
            },
        }
        entry = entries.get(payload["path"])
        if entry is None:
            return web.json_response({"code": 404, "message": "not found"})
        return web.json_response({"code": 200, "data": entry})

    async def _download(self, request):
        self.assertEqual(request.match_info["path"], "source/poster.jpg")
        self.assertEqual(request.query["sign"], "image-sign")
        return web.Response(body=b"image")

    async def test_generates_strm_and_downloads_selected_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            task = Alist2StrmTask(
                uuid="test-strm",
                source_dir="/source",
                target_dir="library",
                image=True,
                max_workers=2,
                max_downloaders=1,
            )
            result = await Alist2StrmService(
                task=task,
                alist_url=self.alist_url,
                api_key="test-key",
                request_timeout=2,
                output_root=directory,
            ).run()

            output = Path(directory) / "library"
            self.assertEqual(
                (output / "movie.strm").read_text(encoding="utf-8"),
                f"{self.alist_url}/d/source/movie.mkv?sign=video-sign",
            )
            self.assertEqual((output / "poster.jpg").read_bytes(), b"image")
            self.assertEqual(result["strm_created"], 1)
            self.assertEqual(result["assets_downloaded"], 1)
            self.assertEqual(result["failed"], 0)

    async def test_incremental_run_fetches_only_changed_path(self):
        with tempfile.TemporaryDirectory() as directory:
            task = Alist2StrmTask(
                uuid="incremental-strm",
                source_dir="/source",
                target_dir="library",
            )
            result = await Alist2StrmService(
                task=task,
                alist_url=self.alist_url,
                api_key="test-key",
                request_timeout=2,
                output_root=directory,
                changed_paths=["/source/movie.mkv", "/source/movie.mkv"],
            ).run()

            output = Path(directory) / "library/movie.strm"
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                f"{self.alist_url}/d/source/movie.mkv?sign=video-sign",
            )
            self.assertEqual(self.get_paths, ["/source/movie.mkv"])
            self.assertTrue(result["incremental"])
            self.assertEqual(result["requested_path_count"], 1)
            self.assertEqual(result["scanned"], 1)
            self.assertEqual(result["strm_created"], 1)
            self.assertEqual(result["failed"], 0)


if __name__ == "__main__":
    unittest.main()
