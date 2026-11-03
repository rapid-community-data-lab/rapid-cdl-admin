import asyncio
import re
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from .config import Settings
from .netutil import UrlNotAllowed, check_url_target
from .util import safe_filename

RETRIABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
REDIRECT_STATUS = {301, 302, 303, 307, 308}


class DownloadError(Exception):
    def __init__(self, message: str, retriable: bool):
        super().__init__(message)
        self.retriable = retriable


class Interrupted(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def parse_content_range(value: str | None) -> tuple[int | None, int | None]:
    match = re.match(r"\s*bytes\s+(\d+)-(-?\d+)/(\d+|\*)", value or "")
    if not match:
        return None, None
    total = None if match.group(3) == "*" else int(match.group(3))
    return int(match.group(1)), total


def filename_from_headers(headers: httpx.Headers) -> str | None:
    disposition = headers.get("content-disposition", "")
    match = re.search(r"filename\*\s*=\s*[A-Za-z0-9_-]+''([^;]+)", disposition)
    if match:
        return safe_filename(unquote(match.group(1).strip().strip('"')))
    match = re.search(r'filename\s*=\s*"([^"]+)"', disposition) or re.search(r"filename\s*=\s*([^;]+)", disposition)
    if match:
        return safe_filename(match.group(1).strip())
    return None


def filename_from_url(url: str) -> str | None:
    base = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    if not base or "." not in base:
        return None
    if base.lower().endswith((".php", ".asp", ".aspx", ".jsp", ".cgi", ".html", ".htm")):
        return None
    return safe_filename(base)


class Downloader:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.read_timeout, connect=settings.connect_timeout),
            follow_redirects=False,
            headers={"User-Agent": "rapid-cdl-data-ingester/1.0", "Accept-Encoding": "identity"},
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _open(self, url: str, headers: dict) -> httpx.Response:
        current = url
        for _ in range(10):
            await check_url_target(current, self.settings)
            request = self.client.build_request("GET", current, headers=headers)
            response = await self.client.send(request, stream=True)
            location = response.headers.get("location")
            if response.status_code in REDIRECT_STATUS and location:
                await response.aclose()
                current = urljoin(current, location)
                continue
            return response
        raise DownloadError("The link redirected too many times.", False)

    async def _with_retries(self, action: Callable, on_retry: Callable, cancelled: Callable):
        attempts = 0
        while True:
            try:
                return await action()
            except (Interrupted, UrlNotAllowed):
                raise
            except (httpx.TransportError, ConnectionError, DownloadError) as exc:
                retriable = not isinstance(exc, DownloadError) or exc.retriable
                if not retriable:
                    raise
                attempts += 1
                if attempts > self.settings.download_retries:
                    raise DownloadError(f"Gave up after {attempts} attempts: {exc}", True) from exc
                delay = min(self.settings.retry_backoff_max, 2 ** min(attempts, 6))
                on_retry(attempts, str(exc) or exc.__class__.__name__, delay)
                await self.sleep(delay, cancelled)

    async def sleep(self, seconds: float, cancelled: Callable) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            reason = cancelled()
            if reason:
                raise Interrupted(reason)
            await asyncio.sleep(min(0.5, max(0.0, deadline - time.monotonic())))

    async def probe(self, url: str, on_retry: Callable, cancelled: Callable) -> dict:
        async def action():
            response = await self._open(url, {})
            try:
                code = response.status_code
                if code in RETRIABLE_STATUS:
                    raise DownloadError(f"The source answered HTTP {code}.", True)
                if code == 206:
                    _, total = parse_content_range(response.headers.get("content-range"))
                    ranges = True
                elif code == 200:
                    length = response.headers.get("content-length")
                    total = int(length) if length and length.isdigit() else None
                    accept = response.headers.get("accept-ranges", "").lower()
                    ranges = True if accept == "bytes" else False if accept == "none" else None
                else:
                    raise DownloadError(f"The source answered HTTP {code}; the link may have expired.", False)
                content_type = response.headers.get("content-type", "")
                if content_type.startswith("text/html"):
                    raise DownloadError("The link returned a web page instead of a file; the link may have expired.", False)
                return {
                    "size": total,
                    "ranges": ranges,
                    "filename": filename_from_headers(response.headers) or filename_from_url(url),
                    "contentType": content_type,
                }
            finally:
                await response.aclose()

        return await self._with_retries(action, on_retry, cancelled)

    async def fetch(
        self,
        url: str,
        dest: Path,
        expected_size: int | None,
        on_progress: Callable[[int, int | None], None],
        on_retry: Callable,
        cancelled: Callable,
    ) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        state = {"size": expected_size}

        async def action():
            offset = dest.stat().st_size if dest.exists() else 0
            size = state["size"]
            if size is not None and offset == size:
                return offset
            if size is not None and offset > size:
                dest.unlink()
                offset = 0
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            response = await self._open(url, headers)
            progressed = False
            try:
                code = response.status_code
                if code == 416 and offset:
                    match = re.search(r"/(\d+)\s*$", response.headers.get("content-range", ""))
                    total = int(match.group(1)) if match else None
                    if total is not None and offset == total:
                        state["size"] = total
                        return offset
                    dest.unlink()
                    raise DownloadError("The source rejected the resume position; restarting the file.", True)
                if code in RETRIABLE_STATUS:
                    raise DownloadError(f"The source answered HTTP {code}.", True)
                if code not in (200, 206):
                    raise DownloadError(f"The source answered HTTP {code}; the link may have expired.", False)
                if code == 206:
                    start, total = parse_content_range(response.headers.get("content-range"))
                    if start is not None and start != offset:
                        dest.unlink(missing_ok=True)
                        raise DownloadError("The source returned an unexpected byte range; restarting the file.", True)
                    mode = "ab"
                else:
                    length = response.headers.get("content-length")
                    total = int(length) if length and length.isdigit() else None
                    offset = 0
                    mode = "wb"
                if response.headers.get("content-type", "").startswith("text/html"):
                    raise DownloadError("The link returned a web page instead of a file; the link may have expired.", False)
                if total is not None:
                    state["size"] = total
                on_progress(offset, state["size"])
                with open(dest, mode) as handle:
                    async for chunk in response.aiter_raw():
                        reason = cancelled()
                        if reason:
                            raise Interrupted(reason)
                        handle.write(chunk)
                        offset += len(chunk)
                        progressed = True
                        on_progress(offset, state["size"])
                if state["size"] is not None and offset < state["size"]:
                    raise DownloadError("The connection closed before the file was complete.", True)
                if state["size"] is not None and offset > state["size"]:
                    dest.unlink()
                    raise DownloadError("More data arrived than expected; restarting the file.", True)
                if state["size"] is None:
                    state["size"] = offset
                return offset
            except (httpx.TransportError, DownloadError) as exc:
                if progressed and (not isinstance(exc, DownloadError) or exc.retriable):
                    raise _Progressed(exc) from exc
                raise
            finally:
                await response.aclose()

        attempts = 0
        while True:
            try:
                return await action()
            except (Interrupted, UrlNotAllowed):
                raise
            except _Progressed as wrapped:
                attempts = 0
                on_retry(0, str(wrapped.inner) or wrapped.inner.__class__.__name__, 1)
                await self.sleep(1, cancelled)
            except (httpx.TransportError, ConnectionError, DownloadError) as exc:
                retriable = not isinstance(exc, DownloadError) or exc.retriable
                if not retriable:
                    raise
                attempts += 1
                if attempts > self.settings.download_retries:
                    raise DownloadError(f"Gave up after {attempts} attempts: {exc}", True) from exc
                delay = min(self.settings.retry_backoff_max, 2 ** min(attempts, 6))
                on_retry(attempts, str(exc) or exc.__class__.__name__, delay)
                await self.sleep(delay, cancelled)


class _Progressed(Exception):
    def __init__(self, inner: Exception):
        super().__init__(str(inner))
        self.inner = inner
