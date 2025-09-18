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
allow_origin_regex = os.getenv("CORS_ORIGIN_REGEX")

# With wildcard origins, browsers disallow credentials; only enable credentials when origins are explicit.
allow_credentials = allow_origins != ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_origin_regex=allow_origin_regex,
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
    # Limits and safeguards
    app.state.max_upload_bytes = int(os.getenv("MAX_UPLOAD_BYTES", str(1 * 1024 * 1024)))  # 1 MiB
    app.state.max_remote_bytes = int(os.getenv("MAX_REMOTE_BYTES", str(1 * 1024 * 1024)))  # 1 MiB
    app.state.url_require_https = os.getenv("URL_REQUIRE_HTTPS", "1") not in {"0", "false", "False"}

    # PokeAPI client
    app.state.pokeapi = PokeAPIClient(timeout=8.0)


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


