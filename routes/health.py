from fastapi import APIRouter, Request


router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    pokeapi_ready = bool(getattr(request.app.state, "pokeapi", None))
    return {"status": "ok", "pokeapi": pokeapi_ready}

