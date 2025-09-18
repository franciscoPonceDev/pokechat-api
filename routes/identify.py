from __future__ import annotations

from typing import List, Optional, Tuple
import re

from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile
from fastapi.responses import Response

from services.image_verification import (
    classify_similarity,
    compute_image_hash,
    compute_image_hash_variants,
    hamming_distance,
    similarity_from_distance,
)
from services.pokeapi import PokeAPIClient


router = APIRouter()


def _stats_table(stats: dict) -> List[str]:
    order = ["hp", "attack", "defense", "special-attack", "special-defense", "speed"]
    headers = ["HP", "Attack", "Defense", "Sp. Atk", "Sp. Def", "Speed"]
    values = [str(stats.get(k, "-")) for k in order]
    return [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["----"] * len(headers)) + "|",
        "| " + " | ".join(values) + " |",
    ]


def _format_identified_markdown(pokemon: dict, species: Optional[dict], status: str, similarity: float) -> str:
    name = (pokemon or {}).get("name") or "Unknown"
    title = name.replace("-", " ").title()
    types = ", ".join([t.get("type", {}).get("name", "").replace("-", " ").title() for t in (pokemon.get("types") or []) if t])
    abilities = [a.get("ability", {}).get("name", "") for a in (pokemon.get("abilities") or [])]
    stats = {s.get("stat", {}).get("name", ""): s.get("base_stat") for s in (pokemon.get("stats") or [])}
    sprite = (pokemon.get("sprites") or {}).get("front_default")

    fun_fact = None
    if species and isinstance(species.get("flavor_text_entries"), list):
        for entry in species["flavor_text_entries"]:
            if (entry.get("language") or {}).get("name") == "en":
                fun_fact = (entry.get("flavor_text") or "").replace("\n", " ").replace("\f", " ").strip()
                break

    lines: List[str] = []
    lines.append(f"## {title}")
    lines.append("")
    lines.append(f"- Verification: **{status}**")
    lines.append(f"- Similarity: **{similarity:.4f}**")
    if sprite:
        lines.append("")
        lines.append(f"![{title}]({sprite})")
    if types:
        lines.append("")
        lines.append(f"{title} is a {types}-type Pokémon.")
    if fun_fact:
        lines.append("")
        lines.append(fun_fact)
    if abilities:
        lines.append("")
        lines.append("**Abilities:**")
        for a in abilities:
            if a:
                lines.append(f"- {a.replace('-', ' ').title()}")
    if stats:
        lines.append("")
        lines.append("**Base Stats:**")
        lines.extend(_stats_table(stats))
    return "\n".join(lines)


@router.post("/identify")
async def identify(request: Request, file: Optional[UploadFile] = File(None), url: Optional[str] = Body(None)) -> Response:
    method = getattr(request.app.state, "hash_method", "phash")
    hash_size = getattr(request.app.state, "hash_size", 8)
    threshold = getattr(request.app.state, "similarity_threshold", 0.9)
    api: PokeAPIClient = getattr(request.app.state, "pokeapi", None)
    if not api:
        raise HTTPException(status_code=503, detail="PokeAPI client not ready")

    # Support JSON body or query param for URL
    if (file is None) and (not url):
        # Try to read from JSON body explicitly (in case the optional File parameter led to multipart expectation)
        try:
            if "application/json" in (request.headers.get("content-type") or "").lower():
                payload = await request.json()
                if isinstance(payload, dict):
                    url = payload.get("url") or url
        except Exception:
            pass
        # Try query string as last resort
        if not url:
            url = request.query_params.get("url")
    if (file is None) and (not url):
        raise HTTPException(status_code=400, detail="Provide either a file upload (form-data 'file') or a 'url' field (JSON body or query param)")
    if (file is not None) and url:
        raise HTTPException(status_code=400, detail="Provide only one of 'file' or 'url', not both")

    # Load image bytes from either upload or URL with strict limits
    try:
        file_bytes: Optional[bytes] = None
        if url:
            # Sanitize common prefix/surrounding characters users might paste, e.g., "@https://...", <https://...>
            if isinstance(url, str):
                cleaned = url.strip()
                cleaned = re.sub(r"^@+", "", cleaned)
                cleaned = cleaned.strip(" <>\"'\t\r\n")
                url = cleaned
            require_https = bool(getattr(request.app.state, "url_require_https", True))
            if not (isinstance(url, str) and (url.startswith("https://") or (not require_https and url.startswith("http://")))):
                raise HTTPException(status_code=400, detail="'url' must start with http:// or https://")
            file_bytes = await api.get_bytes(url, max_bytes=int(getattr(request.app.state, "max_remote_bytes", 1048576)))
            if not file_bytes:
                raise HTTPException(status_code=400, detail="Failed to fetch image from URL")
        else:
            # file path
            if not (file.content_type or "").startswith("image/"):
                raise HTTPException(status_code=400, detail="Uploaded file must be an image")
            # enforce max upload size
            max_upload = int(getattr(request.app.state, "max_upload_bytes", 1048576))
            file_bytes = await file.read(max_upload + 1)
            if len(file_bytes) > max_upload:
                raise HTTPException(status_code=413, detail="File too large")
            if not file_bytes:
                raise HTTPException(status_code=400, detail="Empty file")

        query_hash = compute_image_hash(file_bytes, method=method, hash_size=hash_size)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to process image: {exc}") from exc

    # Fast pass: check default sprites only with bounded concurrency
    entries = await api.list_pokemon_entries(limit=2000)
    name_to_id = {e.get('name'): e.get('id') for e in entries if e.get('name') and e.get('id')}
    best_name: Optional[str] = None
    best_similarity = 0.0
    bit_length = int(hash_size * hash_size)

    from asyncio import Semaphore, gather
    sem = Semaphore(16)

    async def eval_entry(entry: dict) -> Tuple[str, float]:
        name = entry.get('name')
        pid = entry.get('id')
        if not name or not pid:
            return '', 0.0
        url = api.sprite_default_url_for_id(pid)
        async with sem:
            sprite_hash = await api.sprite_hash_from_url(url, method=method, hash_size=hash_size)
        if not sprite_hash:
            return name, 0.0
        dist = hamming_distance(query_hash, sprite_hash)
        sim = similarity_from_distance(dist, bit_length)
        return name, sim

    results = await gather(*[eval_entry(e) for e in entries])
    # Keep top-K candidates for refinement
    topk = sorted([(n, s) for n, s in results if n], key=lambda t: t[1], reverse=True)[:50]
    if topk:
        best_name, best_similarity = topk[0]

    # If confidence is low, refine the top-K using multiple sprite variants and hash methods
    if (best_similarity < threshold) and topk:
        methods = ["phash", "dhash", "whash"]
        # Precompute multiple query hashes per method and crop ratio (robust to backgrounds)
        qh_variants = compute_image_hash_variants(file_bytes, methods=methods, hash_size=hash_size)

        async def score_candidate(name: str) -> Tuple[str, float]:
            pid = name_to_id.get(name)
            if not pid:
                return name, 0.0
            # Expand to many sprite sources, including PokemonDB patterns
            urls = await api.sprite_urls_for_pokemon_all(name, include_pokemondb=True, max_urls=60)
            local_best = 0.0
            for url in urls:
                # Hash each variant with all methods (cached + bounded by semaphore)
                for m in methods:
                    async with sem:
                        h = await api.sprite_hash_from_url(url, method=m, hash_size=hash_size)
                    if not h:
                        continue
                    for qh in qh_variants.get(m, []):
                        dist = hamming_distance(qh, h)
                        sim = similarity_from_distance(dist, bit_length)
                        if sim > local_best:
                            local_best = sim
            return name, local_best

        refined = await gather(*[score_candidate(n) for n, _ in topk])
        refined_sorted = sorted(refined, key=lambda t: t[1], reverse=True)
        if refined_sorted and refined_sorted[0][1] > best_similarity:
            best_name, best_similarity = refined_sorted[0]

    # CLIP re-ranking removed to keep the service lean

    if not best_name:
        raise HTTPException(status_code=404, detail="Could not identify a matching Pokémon sprite.")

    status_text = classify_similarity(best_similarity, threshold)
    pokemon = await api.pokemon(best_name)
    species = await api.species(str(pokemon.get("id"))) if pokemon else None
    md = _format_identified_markdown(pokemon or {}, species, status_text, best_similarity)
    return Response(content=md, media_type="text/markdown")


# Removed legacy /verify alias from previous prototype




