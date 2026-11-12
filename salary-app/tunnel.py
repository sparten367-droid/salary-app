"""Бесплатный туннель Cloudflare: делает локальный API доступным для Mini App по https."""
import asyncio
import logging
import os
import re
import shutil

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
log = logging.getLogger("tunnel")


def find_cloudflared(base: str):
    for name in ("cloudflared.exe", "cloudflared"):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return shutil.which("cloudflared")


class Tunnel:
    def __init__(self, base: str, port: int):
        self.base, self.port = base, port
        self.proc = None
        self.url = None

    async def start(self, timeout: float = 45) -> str | None:
        exe = find_cloudflared(self.base)
        if not exe:
            log.warning("cloudflared не найден — приложение не будет доступно")
            return None
        self.proc = await asyncio.create_subprocess_exec(
            exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        found = asyncio.get_running_loop().create_future()

        async def drain():
            while True:
                line = await self.proc.stderr.readline()
                if not line:
                    break
                m = URL_RE.search(line.decode(errors="ignore"))
                if m and not found.done():
                    found.set_result(m.group(0))
            if not found.done():
                found.set_result(None)

        asyncio.create_task(drain())
        try:
            self.url = await asyncio.wait_for(found, timeout)
        except asyncio.TimeoutError:
            self.url = None
        if self.url:
            await self._wait_dns()
        log.info("tunnel url: %s", self.url)
        return self.url

    async def _wait_dns(self, tries: int = 20):
        """Новый адрес trycloudflare появляется в DNS не сразу."""
        import socket
        host = self.url.split("//")[1]
        for _ in range(tries):
            try:
                await asyncio.get_running_loop().getaddrinfo(host, 443)
                return
            except socket.gaierror:
                await asyncio.sleep(2)

    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def stop(self):
        if self.alive():
            self.proc.terminate()
