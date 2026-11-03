import asyncio
import os

import pytest

from app.downloader import DownloadError, Downloader, Interrupted
from app.netutil import UrlNotAllowed, check_url_syntax
from servers import FileServer


def _run(coro):
    return asyncio.run(coro)


def test_resumes_after_dropped_connection(settings, tmp_path):
    data = os.urandom(3_000_000)
    with FileServer({"crate.zip": data}, drop_after=1_000_000) as server:
        async def go():
            downloader = Downloader(settings)
            try:
                url = server.base + "/redirect/crate.zip"
                info = await downloader.probe(url, lambda *a: None, lambda: None)
                assert info["size"] == len(data) and info["ranges"] is True
                retries = []
                received = await downloader.fetch(
                    url, tmp_path / "part.bin", info["size"], lambda *a: None, lambda *a: retries.append(a), lambda: None
                )
                return received, retries
            finally:
                await downloader.close()

        received, retries = _run(go())
        assert received == len(data)
        assert (tmp_path / "part.bin").read_bytes() == data
        assert retries
        ranges = [header for name, header in server.requests if header]
        assert ranges and ranges[-1].startswith("bytes=") and ranges[-1] != "bytes=0-"


def test_filesender_style_headers():
    from app.downloader import parse_content_range

    assert parse_content_range("bytes 0--1/1398149467") == (0, 1398149467)
    assert parse_content_range("bytes 1000-1998/1398149467") == (1000, 1398149467)
    assert parse_content_range("bytes */100") == (None, None)


def test_resumes_existing_partial_file(settings, tmp_path):
    data = os.urandom(500_000)
    (tmp_path / "part.bin").write_bytes(data[:123_456])
    with FileServer({"crate.zip": data}) as server:
        async def go():
            downloader = Downloader(settings)
            try:
                return await downloader.fetch(server.base + "/files/crate.zip", tmp_path / "part.bin", len(data), lambda *a: None, lambda *a: None, lambda: None)
            finally:
                await downloader.close()

        assert _run(go()) == len(data)
        assert server.requests[-1][1] == "bytes=123456-"
        assert (tmp_path / "part.bin").read_bytes() == data


def test_server_without_ranges_restarts(settings, tmp_path):
    data = os.urandom(200_000)
    (tmp_path / "part.bin").write_bytes(b"stale-bytes")
    with FileServer({"crate.zip": data}, ranges=False) as server:
        async def go():
            downloader = Downloader(settings)
            try:
                return await downloader.fetch(server.base + "/files/crate.zip", tmp_path / "part.bin", None, lambda *a: None, lambda *a: None, lambda: None)
            finally:
                await downloader.close()

        assert _run(go()) == len(data)
        assert (tmp_path / "part.bin").read_bytes() == data


def test_missing_file_is_fatal(settings, tmp_path):
    with FileServer({}) as server:
        async def go():
            downloader = Downloader(settings)
            try:
                await downloader.probe(server.base + "/files/none.zip", lambda *a: None, lambda: None)
            finally:
                await downloader.close()

        with pytest.raises(DownloadError) as error:
            _run(go())
        assert not error.value.retriable


def test_pause_interrupts(settings, tmp_path):
    data = os.urandom(4_000_000)
    with FileServer({"crate.zip": data}) as server:
        async def go():
            downloader = Downloader(settings)
            try:
                await downloader.fetch(server.base + "/files/crate.zip", tmp_path / "part.bin", len(data), lambda *a: None, lambda *a: None, lambda: "pause")
            finally:
                await downloader.close()

        with pytest.raises(Interrupted):
            _run(go())


def test_url_rules(settings):
    with pytest.raises(UrlNotAllowed):
        check_url_syntax("ftp://example.org/x.zip", settings)
    with pytest.raises(UrlNotAllowed):
        check_url_syntax("https://user:pw@example.org/x.zip", settings)
    assert check_url_syntax(" https://filesender.aarnet.edu.au/download.php?token=x ", settings).startswith("https://")
