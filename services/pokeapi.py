from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional
import re

import httpx

from .image_verification import compute_image_hash


class SimpleTTLCache:
    def __init__(self, ttl_seconds: float = 300.0) -> None:
        self._ttl = ttl_seconds
        self._store: Dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        now = time.time()
        value = self._store.get(key)
        if not value:
            return None
        expires_at, data = value
        if expires_at < now:
            self._store.pop(key, None)
            return None
        return data

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.time() + self._ttl, value)


class PokeAPIClient:
    def __init__(self, base_url: str = "https://pokeapi.co/api/v2", timeout: float = 20.0, ttl_seconds: float = 600.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)
        self._cache = SimpleTTLCache(ttl_seconds=ttl_seconds)
        # cache for raw bytes and computed sprite hashes
        self._bytes_cache = SimpleTTLCache(ttl_seconds=ttl_seconds)
        self._sprite_hash_cache: Dict[str, Any] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def get_json(self, path: str) -> Any:
        url = f"{self._base_url}/{path.lstrip('/')}"
        cached = self._cache.get(url)
        if cached is not None:
            return cached
        res = await self._client.get(url)
        res.raise_for_status()
        data = res.json()
        self._cache.set(url, data)
        return data

    async def get_bytes(self, url: str) -> Optional[bytes]:
        if not url:
            return None
        cached = self._bytes_cache.get(url)
        if cached is not None:
            return cached
        # Use a browser-like UA and allow redirects for sites like Pinterest/Imgix/CDNs
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": url,
        }
        res = await self._client.get(url, headers=headers, follow_redirects=True)
        if res.status_code != 200:
            return None
        data = res.content
        self._bytes_cache.set(url, data)
        return data

    async def try_get_json(self, path: str) -> Optional[Any]:
        try:
            return await self.get_json(path)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    # Specific helpers
    async def pokemon(self, name_or_id: str) -> Optional[dict]:
        return await self.try_get_json(f"pokemon/{name_or_id}")

    async def species(self, name_or_id: str) -> Optional[dict]:
        return await self.try_get_json(f"pokemon-species/{name_or_id}")

    async def type(self, name_or_id: str) -> Optional[dict]:
        return await self.try_get_json(f"type/{name_or_id}")

    async def move(self, name_or_id: str) -> Optional[dict]:
        return await self.try_get_json(f"move/{name_or_id}")

    async def ability(self, name_or_id: str) -> Optional[dict]:
        return await self.try_get_json(f"ability/{name_or_id}")

    async def item(self, name_or_id: str) -> Optional[dict]:
        return await self.try_get_json(f"item/{name_or_id}")

    async def berry(self, name_or_id: str) -> Optional[dict]:
        return await self.try_get_json(f"berry/{name_or_id}")

    async def list_named(self, endpoint: str, limit: int = 2000) -> dict:
        return await self.get_json(f"{endpoint}?limit={limit}&offset=0")

    async def list_pokemon_names(self, limit: int = 2000) -> list[str]:
        data = await self.list_named("pokemon", limit=limit)
        results = data.get("results", []) if isinstance(data, dict) else []
        names = [r.get("name") for r in results if isinstance(r, dict) and r.get("name")]
        return names

    @staticmethod
    def _parse_pokemon_id_from_url(url: Optional[str]) -> Optional[int]:
        if not url or not isinstance(url, str):
            return None
        m = re.search(r"/pokemon/(\d+)/?$", url)
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

    async def list_pokemon_entries(self, limit: int = 2000) -> List[Dict[str, Any]]:
        data = await self.list_named("pokemon", limit=limit)
        results = data.get("results", []) if isinstance(data, dict) else []
        entries: List[Dict[str, Any]] = []
        for r in results:
            if not isinstance(r, dict):
                continue
            name = r.get("name")
            url = r.get("url")
            pid = self._parse_pokemon_id_from_url(url)
            if name and pid:
                entries.append({"name": name, "id": pid})
        return entries

    async def sprite_url_for_pokemon(self, name_or_id: str) -> Optional[str]:
        p = await self.pokemon(name_or_id)
        if not p:
            return None
        sprites = p.get("sprites") or {}
        # prefer front_default
        return sprites.get("front_default")

    async def sprite_hash_for_pokemon(self, name_or_id: str, method: str = "phash", hash_size: int = 8) -> Optional[Any]:
        key = f"sprite_hash::{method}::{hash_size}::{str(name_or_id).lower()}"
        cached = self._sprite_hash_cache.get(key)
        if cached is not None:
            return cached
        url = await self.sprite_url_for_pokemon(name_or_id)
        if not url:
            return None
        img_bytes = await self.get_bytes(url)
        if not img_bytes:
            return None
        try:
            h = compute_image_hash(img_bytes, method=method, hash_size=hash_size)
        except Exception:
            return None
        self._sprite_hash_cache[key] = h
        return h

    async def sprite_urls_for_pokemon(self, name_or_id: str) -> List[str]:
        """Collect multiple representative sprite URLs for better matching.

        Preference order: front_default, official-artwork, home, dream_world.
        Duplicates and missing values are filtered out.
        """
        p = await self.pokemon(name_or_id)
        if not p or not isinstance(p, dict):
            return []
        sprites = p.get("sprites") or {}

        urls: List[str] = []
        # 1) core
        core = sprites.get("front_default")
        if core:
            urls.append(core)

        # 2) other → official-artwork, dream_world, home
        other = sprites.get("other") or {}
        official = (other.get("official-artwork") or {}).get("front_default")
        if official:
            urls.append(official)
        home = (other.get("home") or {}).get("front_default")
        if home:
            urls.append(home)
        dream = (other.get("dream_world") or {}).get("front_default")
        if dream:
            urls.append(dream)

        # 3) fallbacks: some forms may exist under versions
        # We avoid deep traversal to keep latency low; above sources are sufficient for most cases.

        # de-duplicate while preserving order
        seen = set()
        unique_urls: List[str] = []
        for u in urls:
            if u and (u not in seen):
                seen.add(u)
                unique_urls.append(u)
        return unique_urls

    @staticmethod
    def _collect_http_urls(obj: Any, out: Optional[List[str]] = None) -> List[str]:
        if out is None:
            out = []
        if obj is None:
            return out
        if isinstance(obj, str):
            if obj.startswith("http://") or obj.startswith("https://"):
                out.append(obj)
            return out
        if isinstance(obj, dict):
            for v in obj.values():
                PokeAPIClient._collect_http_urls(v, out)
            return out
        if isinstance(obj, list):
            for v in obj:
                PokeAPIClient._collect_http_urls(v, out)
            return out
        return out

    @staticmethod
    def _pokemondb_candidate_urls(name: str) -> List[str]:
        # Generate a handful of common sprite set URLs from PokemonDB CDN
        # This covers many mainstream variants without scraping pages.
        slug = (name or "").strip().lower().replace(" ", "-")
        sets = [
            "home/normal",
            "home/shiny",
            "sword-shield/normal",
            "sword-shield/shiny",
            "x-y/normal",
            "x-y/shiny",
            "black-white/normal",
            "black-white/shiny",
            "diamond-pearl/normal",
            "diamond-pearl/shiny",
        ]
        return [f"https://img.pokemondb.net/sprites/{s}/{slug}.png" for s in sets]

    async def sprite_urls_for_pokemon_all(self, name_or_id: str, include_pokemondb: bool = True, max_urls: int = 80) -> List[str]:
        """Return an expanded set of sprite URLs, including versions across generations.

        Optionally append candidate URLs from PokemonDB CDN using known patterns.
        """
        p = await self.pokemon(name_or_id)
        if not p:
            return []
        sprites = p.get("sprites") or {}
        urls = self._collect_http_urls(sprites, [])

        # De-duplicate early
        seen: set[str] = set()
        uniq: List[str] = []
        for u in urls:
            if u and (u not in seen):
                seen.add(u)
                uniq.append(u)

        if include_pokemondb:
            name = (p.get("name") or str(name_or_id)).lower()
            for u in self._pokemondb_candidate_urls(name):
                if u not in seen:
                    uniq.append(u)
                    seen.add(u)

        return uniq[:max_urls]

    async def sprite_hashes_for_pokemon_all(self, name_or_id: str, method: str = "phash", hash_size: int = 8, include_pokemondb: bool = True, max_urls: int = 80) -> List[Any]:
        urls = await self.sprite_urls_for_pokemon_all(name_or_id, include_pokemondb=include_pokemondb, max_urls=max_urls)
        hashes: List[Any] = []
        for url in urls:
            key = f"sprite_hash_url::{method}::{hash_size}::{url}"
            cached = self._sprite_hash_cache.get(key)
            if cached is not None:
                hashes.append(cached)
                continue
            img_bytes = await self.get_bytes(url)
            if not img_bytes:
                continue
            try:
                h = compute_image_hash(img_bytes, method=method, hash_size=hash_size)
            except Exception:
                continue
            self._sprite_hash_cache[key] = h
            hashes.append(h)
        return hashes

    async def sprite_hashes_for_pokemon(self, name_or_id: str, method: str = "phash", hash_size: int = 8) -> List[Any]:
        """Return multiple sprite hashes for a Pokémon, caching per URL.

        This improves matching for screenshots that differ from the default sprite.
        """
        urls = await self.sprite_urls_for_pokemon(name_or_id)
        hashes: List[Any] = []
        for url in urls:
            key = f"sprite_hash_url::{method}::{hash_size}::{url}"
            cached = self._sprite_hash_cache.get(key)
            if cached is not None:
                hashes.append(cached)
                continue
            img_bytes = await self.get_bytes(url)
            if not img_bytes:
                continue
            try:
                h = compute_image_hash(img_bytes, method=method, hash_size=hash_size)
            except Exception:
                continue
            self._sprite_hash_cache[key] = h
            hashes.append(h)
        return hashes

    def sprite_default_url_for_id(self, pokemon_id: int | str) -> str:
        pid = str(pokemon_id).strip()
        return f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{pid}.png"

    def sprite_variant_urls_for_id(self, pokemon_id: int | str) -> List[str]:
        pid = str(pokemon_id).strip()
        urls = [
            f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{pid}.png",
            f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/{pid}.png",
            f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/home/{pid}.png",
        ]
        # de-duplicate preserve order
        seen: set[str] = set()
        uniq: List[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                uniq.append(u)
        return uniq

    async def sprite_hash_from_url(self, url: str, method: str = "phash", hash_size: int = 8) -> Optional[Any]:
        if not url:
            return None
        key = f"sprite_hash_url::{method}::{hash_size}::{url}"
        cached = self._sprite_hash_cache.get(key)
        if cached is not None:
            return cached
        img_bytes = await self.get_bytes(url)
        if not img_bytes:
            return None
        try:
            h = compute_image_hash(img_bytes, method=method, hash_size=hash_size)
        except Exception:
            return None
        self._sprite_hash_cache[key] = h
        return h




