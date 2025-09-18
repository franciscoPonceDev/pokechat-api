# PokeChat API

This is the backend for PokeChat, a FastAPI application that provides AI-powered chat and Pokémon identification services.

## Features

- **Health Check:** An endpoint to monitor the status of the API.
- **Pokémon Chat:** A conversational endpoint that provides information about Pokémon.
- **Image Identification:** Identify Pokémon from an uploaded image file or a URL.
- **Containerized:** Comes with a `Dockerfile` for easy deployment.

## Tech Stack

- [FastAPI](https://fastapi.tiangolo.com/) – A modern, fast (high-performance) web framework for building APIs with Python.
- [Uvicorn](https://www.uvicorn.org/) – An ASGI server, for running the FastAPI application.
- [Pillow](https://python-pillow.org/) – The friendly PIL fork, used for image manipulation.
- [ImageHash](https://github.com/JohannesBuchner/imagehash) – A library for perceptual image hashing.
- [Httpx](https://www.python-httpx.org/) – A fully featured HTTP client for Python.

## Getting Started

To get a local copy up and running, follow these steps.

### Prerequisites

- Python (v3.11 or later)
- pip

### Installation

1. Clone the repo:
   ```sh
   git clone https://github.com/your-username/pokechat.git
   ```
2. Navigate to the backend directory:
   ```sh
   cd pokechat/api
   ```
3. Create and activate a virtual environment (recommended):
   ```sh
   python -m venv venv
   source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
   ```
4. Install Python packages:
   ```sh
   pip install -r requirements.txt
   ```

### Running the Development Server

To start the development server, run:
```sh
uvicorn main:app --reload --port 8000
```
The API will be available at [http://localhost:8000](http://localhost:8000).

## Environment Variables

This project uses environment variables for configuration. You can create a `.env` file in the `api` directory to manage them.

- `PORT`: The port the application will run on. Defaults to `8000`.
- `CORS_ORIGINS`: A comma-separated list of allowed origins for CORS. Defaults to `*`.
- `HASH_METHOD`: The hashing method for image identification (`phash`, `dhash`, etc.). Defaults to `phash`.
- `HASH_SIZE`: The hash size for image identification. Defaults to `8`.
- `SIMILARITY_THRESHOLD`: The similarity threshold for matching images. Defaults to `0.9`.

## API Endpoints

- **`GET /health`**: Checks the health of the API and its connection to the PokeAPI service.
- **`POST /chat`**: Takes a user's question about a Pokémon and returns a detailed answer.
  - **Body:** `{ "question": "Tell me about Pikachu" }`
- **`POST /identify`**: Identifies a Pokémon from an image. Can accept either a file upload or a JSON body with a URL.
  - **As `multipart/form-data`:** with a `file` field containing the image.
  - **As `application/json`:** with a body like `{ "url": "http://example.com/image.png" }`

## Deployment

This API is configured for deployment on [Fly.io](https://fly.io/) using the provided `Dockerfile` and `fly.toml`.

For detailed deployment instructions, refer to the main repository's `README.md` file.
