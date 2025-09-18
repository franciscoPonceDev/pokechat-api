import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from routes.health import router as health_router
from routes.chat import router as chat_router
from routes.identify import router as identify_router
from services.pokeapi import PokeAPIClient


def _parse_cors_origins(value: str) -> list[str]:
    if not value or value.strip() == "*":
        return ["*"]
    return [origin.strip() for origin in value.split(",") if origin.strip()]


app = FastAPI(title="PokeChat API", version="0.3.0")

# CORS configuration (defaults to allowing all)
cors_origins_env = os.getenv("CORS_ORIGINS", "*")
allow_origins = _parse_cors_origins(cors_origins_env)

# With wildcard origins, browsers disallow credentials; only enable credentials when origins are explicit.
allow_credentials = allow_origins != ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event() -> None:
    """Initialize PokeAPI client and hashing defaults for identification."""
    # Defaults used by /identify if not overridden
    app.state.hash_method = os.getenv("HASH_METHOD", "phash")
    app.state.hash_size = int(os.getenv("HASH_SIZE", "8"))
    app.state.similarity_threshold = float(os.getenv("SIMILARITY_THRESHOLD", "0.9"))

    # PokeAPI client
    app.state.pokeapi = PokeAPIClient()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    api: PokeAPIClient | None = getattr(app.state, "pokeapi", None)
    if api:
        await api.close()


# Routers
app.include_router(health_router)
app.include_router(chat_router)
app.include_router(identify_router)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)


