"""PyPI client: latest version and available versions via the JSON API."""
import httpx

from .versioning import sort_versions

PYPI_URL = "https://pypi.org/pypi/{name}/json"


class PyPIClient:
    def __init__(self, timeout: float = 10.0):
        self._client = httpx.Client(timeout=timeout)

    def get_latest(self, name: str) -> str | None:
        try:
            r = self._client.get(PYPI_URL.format(name=name))
            if r.status_code != 200:
                return None
            return r.json().get("info", {}).get("version")
        except httpx.HTTPError:
            return None

    def get_available(self, name: str) -> list[str]:
        try:
            r = self._client.get(PYPI_URL.format(name=name))
            if r.status_code != 200:
                return []
            releases = r.json().get("releases", {})
            versions = [v for v in releases.keys() if releases.get(v)]
            return sort_versions(versions, style="pep440")
        except httpx.HTTPError:
            return []

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass
