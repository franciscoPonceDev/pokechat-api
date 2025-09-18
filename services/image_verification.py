from __future__ import annotations

import os
from io import BytesIO
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import imagehash
from PIL import Image


HashFunc = Callable[[Image.Image, int], imagehash.ImageHash]
ReferenceItem = Tuple[str, imagehash.ImageHash]


def get_hash_function(method: str) -> HashFunc:
    """Map method string to an imagehash function.

    Supported methods: 'phash', 'ahash', 'dhash', 'whash'.
    Defaults to 'phash' if unknown.
    """
    name = (method or "phash").lower()
    if name == "ahash":
        return lambda img, size: imagehash.average_hash(img, hash_size=size)
    if name == "dhash":
        return lambda img, size: imagehash.dhash(img, hash_size=size)
    if name in {"whash", "whash-haar", "whash_haar"}:
        return lambda img, size: imagehash.whash(img, hash_size=size)
    # default
    return lambda img, size: imagehash.phash(img, hash_size=size)


def _normalize_image_for_hash(file_bytes: bytes) -> Image.Image:
    if not file_bytes:
        raise ValueError("No image data provided")
    with Image.open(BytesIO(file_bytes)) as img:
        try:
            img = img.convert("RGBA")
        except Exception:
            img = img.convert("RGB")
            return img
        # Crop to alpha bounding box if available (removes transparent margins common in sprites)
        try:
            alpha = img.getchannel("A")
            bbox = alpha.getbbox()
            if bbox:
                img = img.crop(bbox)
        except Exception:
            pass
        # Composite onto white to avoid black fringes from straight RGB conversion
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        comp = Image.alpha_composite(bg, img)
        return comp.convert("RGB")


def compute_image_hash(file_bytes: bytes, method: str = "phash", hash_size: int = 8) -> imagehash.ImageHash:
    if not file_bytes:
        raise ValueError("No image data provided")
    try:
        img_converted = _normalize_image_for_hash(file_bytes)
        fn = get_hash_function(method)
        return fn(img_converted, hash_size)
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError(f"Unable to read image: {exc}") from exc


def compute_file_hash(path: str, method: str = "phash", hash_size: int = 8) -> Optional[imagehash.ImageHash]:
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        img_converted = _normalize_image_for_hash(data)
        fn = get_hash_function(method)
        return fn(img_converted, hash_size)
    except Exception:
        return None


def load_reference_hashes(directory: str, method: str = "phash", hash_size: int = 8) -> List[ReferenceItem]:
    if not directory or not os.path.isdir(directory):
        return []

    supported_ext = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
    references: List[ReferenceItem] = []

    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        _, ext = os.path.splitext(name.lower())
        if not os.path.isfile(path) or ext not in supported_ext:
            continue

        h = compute_file_hash(path, method=method, hash_size=hash_size)
        if h is None:
            continue
        references.append((name, h))

    return references


def load_reference_hashes_by_category(root_directory: str, method: str = "phash", hash_size: int = 8) -> Dict[str, List[ReferenceItem]]:
    """Load references grouped by subfolder name as category.

    Expected layout:
      root/
        pokemon/
          pikachu.png
          charizard.jpg
        berries/
          cheri-berry.png
    The reference item name is derived from the filename without extension.
    """
    result: Dict[str, List[ReferenceItem]] = {}
    if not root_directory or not os.path.isdir(root_directory):
        return result

    for cat in sorted(os.listdir(root_directory)):
        cat_path = os.path.join(root_directory, cat)
        if not os.path.isdir(cat_path):
            continue
        items: List[ReferenceItem] = []
        supported_ext = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
        for name in sorted(os.listdir(cat_path)):
            path = os.path.join(cat_path, name)
            base, ext = os.path.splitext(name.lower())
            if not os.path.isfile(path) or ext not in supported_ext:
                continue
            h = compute_file_hash(path, method=method, hash_size=hash_size)
            if h is None:
                continue
            items.append((base, h))
        if items:
            result[cat.lower()] = items
    return result


def hamming_distance(hash_a: imagehash.ImageHash, hash_b: imagehash.ImageHash) -> int:
    return int(hash_a - hash_b)


def _bit_length_from_hash(h: imagehash.ImageHash, fallback_hash_size: int) -> int:
    try:
        return int(getattr(h, "hash").size)
    except Exception:
        return int(fallback_hash_size * fallback_hash_size)


def similarity_from_distance(distance: int, bit_length: int) -> float:
    if bit_length <= 0:
        return 0.0
    distance = max(0, min(distance, bit_length))
    return 1.0 - (float(distance) / float(bit_length))


def best_match_similarity(
    query_hash: imagehash.ImageHash,
    reference_hashes: Iterable[ReferenceItem],
    hash_size: int = 8,
) -> Tuple[float, Optional[ReferenceItem]]:
    bit_length = _bit_length_from_hash(query_hash, hash_size)

    best_item: Optional[ReferenceItem] = None
    best_similarity = 0.0

    for item in reference_hashes:
        name, ref_hash = item
        dist = hamming_distance(query_hash, ref_hash)
        sim = similarity_from_distance(dist, bit_length)
        if sim > best_similarity:
            best_similarity = sim
            best_item = item

    return best_similarity, best_item


def _center_crop(img: Image.Image, crop_ratio: float) -> Image.Image:
    if crop_ratio >= 0.999:
        return img
    w, h = img.size
    cw = int(w * crop_ratio)
    ch = int(h * crop_ratio)
    if cw <= 0 or ch <= 0:
        return img
    left = (w - cw) // 2
    top = (h - ch) // 2
    right = left + cw
    bottom = top + ch
    return img.crop((left, top, right, bottom))


def compute_image_hash_variants(
    file_bytes: bytes,
    methods: List[str] = ["phash", "dhash", "whash"],
    hash_size: int = 8,
    crop_ratios: List[float] = [1.0, 0.9, 0.8, 0.7],
) -> Dict[str, List[imagehash.ImageHash]]:
    if not file_bytes:
        raise ValueError("No image data provided")
    try:
        base = _normalize_image_for_hash(file_bytes)
        out: Dict[str, List[imagehash.ImageHash]] = {}
        for m in methods:
            fn = get_hash_function(m)
            hashes: List[imagehash.ImageHash] = []
            for r in crop_ratios:
                cropped = _center_crop(base, r)
                try:
                    hashes.append(fn(cropped, hash_size))
                except Exception:
                    continue
            if hashes:
                out[m] = hashes
        return out
    except Exception as exc:
        raise ValueError(f"Unable to read image: {exc}") from exc


def classify_similarity(similarity: float, threshold: float = 0.9) -> str:
    if similarity >= threshold:
        return "Likely Accurate"
    return "Potential Inaccurate"


