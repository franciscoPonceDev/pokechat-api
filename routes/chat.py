from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from services.pokeapi import PokeAPIClient


router = APIRouter()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _title_case_name(name: str) -> str:
    # PokeAPI names are lowercase with optional dashes
    parts = name.replace("-", " ").split()
    return " ".join(p.capitalize() for p in parts)


def _human_join(parts: List[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + f" and {parts[-1]}"


def _is_list_request(question: str) -> bool:
    q = _normalize(question)
    triggers = [
        "list",
        "show",
        "give",
        "some",
        "few",
        "suggest",
        "find",
    ]
    return any(t in q for t in triggers)


def _extract_count(question: str, default: int = 5, max_count: int = 50) -> int:
    q = _normalize(question)
    m = re.search(r"\b(\d{1,3})\b", q)
    if not m:
        return default
    try:
        n = int(m.group(1))
    except Exception:
        return default
    if n < 1:
        return default
    return min(n, max_count)


def _extract_type_name(question: str) -> Optional[str]:
    q = _normalize(question)
    # Known Pokémon types
    types = {
        "normal",
        "fire",
        "water",
        "grass",
        "electric",
        "ice",
        "fighting",
        "poison",
        "ground",
        "flying",
        "psychic",
        "bug",
        "rock",
        "ghost",
        "dragon",
        "dark",
        "steel",
        "fairy",
    }
    for t in types:
        if re.search(rf"\b{re.escape(t)}\b", q):
            return t
    return None


def _compose_message(question: str, resource: str, data: dict) -> str:
    q = _normalize(question)

    if resource == "pokemon":
        name = _title_case_name(data.get("name", "this Pokémon"))

        # Types
        if ("type" in q) or ("types" in q):
            types = [t.get("type", {}).get("name", "").capitalize() for t in data.get("types", []) if t.get("type", {}).get("name")]
            if not types:
                return f"I couldn't find types for {name}. Do you want me to get more data about this Pokémon?"
            types_text = _human_join(types)
            if len(types) == 1:
                return f"{name}'s type is {types_text}. Do you want me to get more data about this Pokémon?"
            return f"{name}'s types are {types_text}. Do you want me to get more data about this Pokémon?"

        # Abilities
        if ("ability" in q) or ("abilities" in q):
            abilities = [a.get("ability", {}).get("name", "").replace("-", " ").title() for a in data.get("abilities", []) if a.get("ability", {}).get("name")]
            if abilities:
                return f"{name}'s abilities are {_human_join(abilities)}. Do you want more details (types, stats, moves)?"
            return f"I couldn't find abilities for {name}. Do you want me to get more data about this Pokémon?"

        # Moves
        if ("move" in q) or ("moves" in q):
            moves = [m.get("move", {}).get("name", "").replace("-", " ").title() for m in (data.get("moves") or [])]
            if moves:
                preview = ", ".join(moves[:3]) + ("…" if len(moves) > 3 else "")
                return f"{name} has {len(moves)} moves, e.g., {preview}. Want stats or abilities too?"
            return f"I couldn't find moves for {name}. Do you want me to get more data about this Pokémon?"

        # Stats
        if ("stat" in q) or ("stats" in q):
            stats = {s.get("stat", {}).get("name", ""): s.get("base_stat") for s in data.get("stats", [])}
            if stats:
                subset = [f"{k.replace('-', ' ').title()}: {v}" for k, v in list(stats.items())[:3]]
                snippet = "; ".join(subset)
                return f"Some of {name}'s base stats are {snippet}. Want the full list or abilities?"
            return f"I couldn't find stats for {name}. Do you want me to get more data about this Pokémon?"

        # Generic fallback for Pokémon
        return f"Here's basic info about {name}. Do you want types, abilities, stats, or moves?"

    # Non-Pokémon resources (generic message)
    if resource == "type":
        type_name = _title_case_name(data.get("name", "this type"))
        # Summarize damage relations if available
        rel = (data or {}).get("damage_relations") or {}
        def names(key: str) -> List[str]:
            arr = rel.get(key) or []
            vals: List[str] = []
            for a in arr:
                n = (a or {}).get("name")
                if n:
                    vals.append(n.replace("-", " ").title())
            return vals
        lines: List[str] = [f"## {type_name} Type"]
        pairs = [
            ("Double damage to", names("double_damage_to")),
            ("Double damage from", names("double_damage_from")),
            ("Half damage to", names("half_damage_to")),
            ("Half damage from", names("half_damage_from")),
            ("No damage to", names("no_damage_to")),
            ("No damage from", names("no_damage_from")),
        ]
        for label, arr in pairs:
            if arr:
                lines.append(f"- {label}: {_human_join(arr)}")
        return "\n".join(lines)
    if resource == "ability":
        ability_name = data.get("name", "this ability").replace("-", " ").title()
        eff = ""
        for e in (data.get("effect_entries") or []):
            if (e.get("language") or {}).get("name") == "en":
                eff = (e.get("short_effect") or e.get("effect") or "").replace("\n", " ")
                break
        lines = [f"## {ability_name} (Ability)"]
        if eff:
            lines.append("")
            lines.append(eff)
        return "\n".join(lines)
    if resource == "move":
        move_name = data.get("name", "this move").replace("-", " ").title()
        mtype = ((data.get("type") or {}).get("name") or "").replace("-", " ").title()
        dmg = ((data.get("damage_class") or {}).get("name") or "").replace("-", " ").title()
        power = data.get("power")
        accuracy = data.get("accuracy")
        pp = data.get("pp")
        priority = data.get("priority")
        eff = ""
        for e in (data.get("effect_entries") or []):
            if (e.get("language") or {}).get("name") == "en":
                eff = (e.get("short_effect") or e.get("effect") or "").replace("\n", " ")
                # Replace effect chance placeholder, if present
                ch = data.get("effect_chance")
                if ch is not None:
                    eff = eff.replace("$effect_chance", str(ch))
                break
        lines: List[str] = [f"## {move_name} (Move)"]
        info: List[str] = []
        if mtype:
            info.append(f"Type: {mtype}")
        if dmg:
            info.append(f"Class: {dmg}")
        if power is not None:
            info.append(f"Power: {power}")
        if accuracy is not None:
            info.append(f"Accuracy: {accuracy}")
        if pp is not None:
            info.append(f"PP: {pp}")
        if priority not in (None, 0):
            info.append(f"Priority: {priority}")
        if info:
            lines.append("- " + " | ".join(info))
        if eff:
            lines.append("")
            lines.append(eff)
        return "\n".join(lines)

    # Default catch-all
    return "I found what you asked for. Do you want more details?"


async def _try_pokeapi_lookup(api: PokeAPIClient, resource: str, candidate: str) -> Optional[dict]:
    if resource == "pokemon":
        return await api.pokemon(candidate)
    if resource == "berry":
        return await api.berry(candidate)
    if resource == "move":
        return await api.move(candidate)
    if resource == "ability":
        return await api.ability(candidate)
    if resource == "item":
        return await api.item(candidate)
    if resource == "type":
        return await api.type(candidate)
    return None


def _extract_candidates(question: str) -> List[str]:
    # Keep alnum and dashes; replace others with spaces
    cleaned = re.sub(r"[^a-z0-9\-\s]", " ", _normalize(question))
    tokens = [t for t in cleaned.split(" ") if t]
    stop = {
        "what",
        "is",
        "are",
        "the",
        "a",
        "an",
        "about",
        "tell",
        "me",
        "list",
        "stats",
        "stat",
        "ability",
        "abilities",
        "type",
        "types",
        "moves",
        "move",
        "pokemon",
        "pokemons",
        "pokémon",
        "item",
        "items",
        "berry",
        "berries",
        "info",
        "weakness",
        "weaknesses",
        "evolution",
        "chain",
        "for",
        "of",
        "to",
        "and",
        "in",
    }
    # Drop pure numbers so we don't confuse counts with Pokémon IDs
    candidates = [t for t in tokens if (t not in stop) and (not t.isdigit())]
    # return most specific tokens first (longer strings first)
    return sorted(set(candidates), key=lambda s: (-len(s), s))


def _resources_by_priority(question: str) -> List[str]:
    q = _normalize(question)
    order: List[str] = []

    # Attribute-style questions often use plurals (e.g., "types of lucario")
    # Prefer the base entity (pokemon) before the attribute resource.
    if "types" in q:
        order.extend(["pokemon", "type"])
    if "abilities" in q:
        order.extend(["pokemon", "ability"])
    if "moves" in q:
        order.extend(["pokemon", "move"])

    # Direct mentions (singular terms) should still prioritize the base entity first
    if any(k in q for k in ["pokemon", "pokémon"]):
        order.append("pokemon")
    if "berry" in q or "berries" in q:
        order.append("berry")
    if "move" in q:
        order.extend(["pokemon", "move"])
    if "ability" in q:
        order.extend(["pokemon", "ability"])
    if "item" in q:
        order.append("item")
    if "type" in q:
        order.extend(["pokemon", "type"])

    # Direct domain terms
    if "berry" in q or "berries" in q:
        # prioritize berry info for questions about berries
        order.append("berry")
    if "move" in q or "tm" in q or "hm" in q:
        order.append("move")

    # default preference if nothing specified
    if not order:
        order = ["pokemon", "move", "ability", "type", "item", "berry"]

    # de-duplicate while keeping order
    seen = set()
    deduped: List[str] = []
    for r in order:
        if r not in seen:
            seen.add(r)
            deduped.append(r)
    return deduped


def _shape_pokemon(p: dict) -> dict:
    return {
        "name": p.get("name"),
        "id": p.get("id"),
        "height": p.get("height"),
        "weight": p.get("weight"),
        "types": [t["type"]["name"] for t in p.get("types", [])],
        "abilities": [a["ability"]["name"] for a in p.get("abilities", [])],
        "stats": {s["stat"]["name"]: s.get("base_stat") for s in p.get("stats", [])},
        "sprites": p.get("sprites", {}).get("front_default"),
        "source": "pokemon",
    }


def _shape_passthrough(name: str, obj: dict) -> dict:
    return {"name": obj.get("name"), "id": obj.get("id"), "source": name, "raw": obj}


def _type_emoji(type_name: str) -> str:
    mapping = {
        "electric": "⚡",
        "fire": "🔥",
        "water": "💧",
        "grass": "🌿",
        "ice": "❄️",
        "fighting": "🥊",
        "poison": "☠️",
        "ground": "🌋",
        "flying": "🕊️",
        "psychic": "🔮",
        "bug": "🐛",
        "rock": "🪨",
        "ghost": "👻",
        "dragon": "🐉",
        "dark": "🌑",
        "steel": "⚙️",
        "fairy": "✨",
        "normal": "⭐",
    }
    return mapping.get((type_name or "").lower(), "")


def _stats_table(stats: Dict[str, Any]) -> List[str]:
    order = [
        "hp",
        "attack",
        "defense",
        "special-attack",
        "special-defense",
        "speed",
    ]
    headers = ["HP", "Attack", "Defense", "Sp. Atk", "Sp. Def", "Speed"]
    values = [str(stats.get(k, "-")) for k in order]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["----"] * len(headers)) + "|",
        "| " + " | ".join(values) + " |",
    ]
    return lines


def _pokemon_markdown(p: dict, species: Optional[dict] = None) -> str:
    name = (p or {}).get("name") or "Unknown"
    title = name.replace("-", " ").title()
    types_list = [t.get("type", {}).get("name", "") for t in (p.get("types") or [])]
    types_text = ", ".join([t.replace("-", " ").title() for t in types_list if t])
    abilities_list = [a.get("ability", {}).get("name", "") for a in (p.get("abilities") or [])]
    stats = {s.get("stat", {}).get("name", ""): s.get("base_stat") for s in (p.get("stats") or [])}
    sprite = (p.get("sprites") or {}).get("front_default")

    fun_fact = None
    if species and isinstance(species.get("flavor_text_entries"), list):
        for entry in species["flavor_text_entries"]:
            lang = (entry.get("language") or {}).get("name")
            if lang == "en":
                text = (entry.get("flavor_text") or "").replace("\n", " ").replace("\f", " ")
                fun_fact = text.strip()
                break

    emoji = _type_emoji(types_list[0]) if types_list else ""

    lines: List[str] = []
    lines.append(f"## {title} {emoji}".rstrip())
    if types_text:
        lines.append(f"{title} is a {types_text}-type Pokémon.")
    if fun_fact:
        lines.append("")
        lines.append(fun_fact)
    if abilities_list:
        lines.append("")
        lines.append("**Abilities:**")
        for a in abilities_list:
            if a:
                lines.append(f"- {a.replace('-', ' ').title()}")
    if stats:
        lines.append("")
        lines.append("**Base Stats:**")
        lines.extend(_stats_table(stats))
    if sprite:
        lines.append("")
        lines.append(f"![{title}]({sprite})")
    return "\n".join(lines)


@router.post("/chat")
async def chat(request: Request, body: Dict[str, Any]) -> Response:
    # Accept either a single 'question' string or a ChatGPT-style 'messages' array
    question = (body or {}).get("question")
    messages = (body or {}).get("messages")

    if messages and isinstance(messages, list):
        # take the last user message as the question
        user_messages = [m for m in messages if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str)]
        if user_messages:
            question = user_messages[-1]["content"]

    if not question or not isinstance(question, str):
        raise HTTPException(status_code=400, detail="Provide a 'question' string or ChatGPT-style 'messages' array with a user message")

    api: PokeAPIClient = getattr(request.app.state, "pokeapi", None)
    if not api:
        raise HTTPException(status_code=503, detail="PokeAPI client not ready")

    # Special handling: list requests like "List 5 grass type pokemons"
    if _is_list_request(question):
        tname = _extract_type_name(question)
        count = _extract_count(question, default=5)
        if tname:
            type_data = await api.type(tname)
            if not type_data:
                raise HTTPException(status_code=404, detail=f"Unknown type '{tname}'")
            entries = type_data.get("pokemon") or []
            names: List[str] = []
            for entry in entries:
                if isinstance(entry, dict):
                    name = ((entry.get("pokemon") or {}).get("name"))
                    if name:
                        names.append(name)
                if len(names) >= count:
                    break
            if not names:
                raise HTTPException(status_code=404, detail=f"No Pokémon found for type '{tname}'")
            emoji = _type_emoji(tname)
            title = f"{tname.title()}-type Pokémon {emoji}".rstrip()
            lines: List[str] = []
            lines.append(f"## {title}")
            lines.append("")
            plural = "Pokémon" if len(names) == 1 else "Pokémon"
            lines.append(f"Here are {len(names)} {tname} type {plural}:")
            for idx, n in enumerate(names, start=1):
                lines.append(f"{idx}. {n.replace('-', ' ').title()}")
            md = "\n".join(lines)
            return Response(content=md, media_type="text/markdown")
        # If list requested without a type, fall back to listing first N Pokémon
        try:
            names = await api.list_pokemon_names(limit=count)
        except Exception:
            names = []
        if names:
            lines: List[str] = ["## Pokémon", "", f"Here are {len(names)} Pokémon:"]
            for idx, n in enumerate(names, start=1):
                lines.append(f"{idx}. {n.replace('-', ' ').title()}")
            return Response(content="\n".join(lines), media_type="text/markdown")

    resources = _resources_by_priority(question)
    candidates = _extract_candidates(question)
    if not candidates:
        raise HTTPException(status_code=400, detail="Could not extract any search terms from question")

    for resource in resources:
        for cand in candidates:
            data = await _try_pokeapi_lookup(api, resource, cand)
            if data:
                if resource == "pokemon":
                    species = await api.species(str(data.get("id")))
                    md = _pokemon_markdown(data, species)
                    return Response(content=md, media_type="text/markdown")
                title = (data.get("name") or resource).replace("-", " ").title()
                md = f"# {title}\n\nSource: **{resource.title()}**"
                return Response(content=md, media_type="text/markdown")

    raise HTTPException(status_code=404, detail="No matching resource found. Try specifying the category (pokemon, berry, move, ability, item, type) and the name.")



