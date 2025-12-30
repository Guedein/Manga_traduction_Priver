from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import time
import cv2
import numpy as np

from app.utils.logger import get_logger

logger = get_logger("ocr_service")

# ---------------------------
# Réglages perf (manga)
# ---------------------------

MAX_WIDTH_FOR_OCR = 2000       # higher width to keep text readable
CACHE_ENABLED = True
CACHE_MAX_SIZE = 10            # max images in cache (avoid memory leak)
DEBUG_TIMINGS = True

# ---------------------------
# Types
# ---------------------------

Box = List[List[int]]              # [[x,y],[x,y],[x,y],[x,y]]
Result = Tuple[str, float, Box]    # (text, conf, box)


@dataclass
class OcrPack:
    img_for_merge: np.ndarray      # image used for OCR coords (after resize/preprocess)
    results: List[Result]          # boxes in the coords of img_for_merge
    orig_img: np.ndarray           # original image (full size)
    scale_to_orig: float           # factor to map OCR coords back to original


# ---------------------------
# Geometry utils
# ---------------------------

def _poly_to_aabb(poly: Box) -> Tuple[int, int, int, int]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))


def _merge_polys(polys: List[Box]) -> Box:
    x1s: List[int] = []
    y1s: List[int] = []
    x2s: List[int] = []
    y2s: List[int] = []
    for poly in polys:
        x1, y1, x2, y2 = _poly_to_aabb(poly)
        x1s.append(x1)
        y1s.append(y1)
        x2s.append(x2)
        y2s.append(y2)
    x1, y1, x2, y2 = min(x1s), min(y1s), max(x2s), max(y2s)
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _box_center(poly: Box) -> Tuple[float, float]:
    x1, y1, x2, y2 = _poly_to_aabb(poly)
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


def _inside(rect: Tuple[int, int, int, int], point: Tuple[float, float], pad: int = 0) -> bool:
    x1, y1, x2, y2 = rect
    px, py = point
    return (x1 - pad) <= px <= (x2 + pad) and (y1 - pad) <= py <= (y2 + pad)


# ---------------------------
# Image utils (perf)
# ---------------------------

def _resize_for_ocr(img_bgr: np.ndarray, max_width: int) -> Tuple[np.ndarray, float]:
    """
    Resize if too wide.
    Returns (img_resized, scale) where scale = new_w / old_w.
    """
    h, w = img_bgr.shape[:2]
    if w <= max_width:
        return img_bgr, 1.0

    scale = max_width / float(w)
    new_w = int(w * scale)
    new_h = int(h * scale)

    resized = cv2.resize(img_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return resized, scale


# ---------------------------
# Bubble detection (level 2)
# ---------------------------

def _detect_bubble_candidates(img_bgr: np.ndarray) -> List[Tuple[int, int, int, int]]:
    h, w = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

    # light areas -> bubbles often white
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    white_ratio = float((th == 255).mean())
    if white_ratio < 0.35:
        th = cv2.bitwise_not(th)

    k = max(3, int(min(h, w) * 0.01))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    closed = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel, iterations=2)

    k2 = max(3, k // 2)
    kernel2 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k2, k2))
    cleaned = cv2.morphologyEx(closed, cv2.MORPH_OPEN, kernel2, iterations=1)

    contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    rects: List[Tuple[int, int, int, int]] = []
    min_area = (h * w) * 0.002
    max_area = (h * w) * 0.60

    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        area = cw * ch
        if area < min_area or area > max_area:
            continue

        ar = cw / max(1, ch)
        if ar > 12 or ar < 0.08:
            continue

        pad = int(min(h, w) * 0.005)
        x1 = max(0, x - pad)
        y1 = max(0, y - pad)
        x2 = min(w - 1, x + cw + pad)
        y2 = min(h - 1, y + ch + pad)
        rects.append((x1, y1, x2, y2))

    rects.sort(key=lambda r: (r[1], r[0]))
    return rects


def _fallback_cluster_lines(results: List[Result]) -> List[Result]:
    if not results:
        return []

    items: List[Tuple[int, int, str, float, Box, Tuple[int, int, int, int]]] = []
    for text, conf, poly in results:
        x1, y1, x2, y2 = _poly_to_aabb(poly)
        items.append((y1, x1, text, conf, poly, (x1, y1, x2, y2)))
    items.sort()

    merged: List[Result] = []
    used = [False] * len(items)

    for i in range(len(items)):
        if used[i]:
            continue

        _y1, _x1, text, conf, poly, rect = items[i]
        used[i] = True

        gx1, gy1, gx2, gy2 = rect
        gtext = [text]
        gconf = [conf]
        gpolys = [poly]

        line_h = (gy2 - gy1)
        max_dy = max(10, int(line_h * 1.6))

        for j in range(i + 1, len(items)):
            if used[j]:
                continue

            _y1b, _x1b, textb, confb, polyb, rectb = items[j]
            bx1, by1, bx2, by2 = rectb

            if abs(by1 - gy2) > max_dy and abs(by1 - gy1) > max_dy:
                continue

            overlap = max(0, min(gx2, bx2) - max(gx1, bx1))
            minw = max(1, min(gx2 - gx1, bx2 - bx1))
            if overlap / minw < 0.15:
                continue

            used[j] = True
            gtext.append(textb)
            gconf.append(confb)
            gpolys.append(polyb)

            gx1 = min(gx1, bx1)
            gy1 = min(gy1, by1)
            gx2 = max(gx2, bx2)
            gy2 = max(gy2, by2)

        merged_poly = _merge_polys(gpolys)
        merged_text = " ".join(gtext)
        merged_conf = float(np.mean(gconf)) if gconf else 0.0
        merged.append((merged_text, merged_conf, merged_poly))

    return merged


def _merge_by_bubbles(img_bgr: np.ndarray, results: List[Result]) -> List[Result]:
    if not results:
        return []

    bubble_rects = _detect_bubble_candidates(img_bgr)
    if not bubble_rects:
        return _fallback_cluster_lines(results)

    assigned: Dict[int, List[Result]] = {}
    unassigned: List[Result] = []

    for text, conf, poly in results:
        cx, cy = _box_center(poly)

        found_idx: Optional[int] = None
        for idx, rect in enumerate(bubble_rects):
            if _inside(rect, (cx, cy), pad=8):
                found_idx = idx
                break

        if found_idx is None:
            unassigned.append((text, conf, poly))
        else:
            assigned.setdefault(found_idx, []).append((text, conf, poly))

    merged: List[Result] = []

    for _, group in assigned.items():
        group_sorted = sorted(group, key=lambda r: _poly_to_aabb(r[2])[1])

        texts = [t for (t, _c, _p) in group_sorted]
        confs = [c for (_t, c, _p) in group_sorted]
        polys = [p for (_t, _c, p) in group_sorted]

        merged_text = " ".join(texts).strip()
        merged_conf = float(np.mean(confs)) if confs else 0.0
        merged_poly = _merge_polys(polys)

        merged.append((merged_text, merged_conf, merged_poly))

    if unassigned:
        merged.extend(_fallback_cluster_lines(unassigned))

    merged.sort(key=lambda r: _poly_to_aabb(r[2])[1])
    return merged


# ---------------------------
# OCRService (avec EasyOCR)
# ---------------------------

class OCRService:
    def __init__(self):
        self._cache: Dict[str, OcrPack] = {}
        self.last_output_img: Optional[np.ndarray] = None

        self.reader: Any = None  # EasyOCR Reader (type: easyocr.Reader)

    def prepare_preview(self, image_path: str, lang_code: str):
        """
        Returns the preprocessed image used for OCR (resize + preprocess),
        to display right after loading (avoid change before/after).
        """
        # Lancer l'OCR (sera mis en cache)
        pack = self._run_raw_ocr(image_path, lang_code)

        # Retourner l'image exacte qui sera affichée après OCR
        return pack.img_for_merge

    def _ensure_reader(self, lang_code: str) -> None:
        """Initialize EasyOCR reader (lazy load)"""
        if self.reader is not None:
            return

        try:
            import easyocr  # type: ignore
        except ImportError:
            raise RuntimeError(
                "EasyOCR n'est pas installé. Installez-le avec: pip install easyocr"
            )

        # Map lang codes
        lang_map = {
            "auto": ["en", "ch_sim", "ja", "ko"],
            "en": ["en"],
            "ch": ["ch_sim"],
            "jp": ["ja"],
            "kr": ["ko"],
        }

        langs = lang_map.get(lang_code, ["en"])

        if DEBUG_TIMINGS:
            logger.info(f"🔧 Initializing EasyOCR with languages: {langs}")

        try:
            # GPU detection
            import torch
            use_gpu = torch.cuda.is_available()
            if DEBUG_TIMINGS:
                logger.info(f"   GPU available: {use_gpu}")
        except Exception:
            use_gpu = False

        self.reader = easyocr.Reader(langs, gpu=use_gpu)

        if DEBUG_TIMINGS:
            logger.info(f"✅ EasyOCR initialized (GPU: {use_gpu})")

    def _run_raw_ocr(self, img_path: str, lang_code: str) -> OcrPack:
        t0 = time.perf_counter()

        # Cache
        cache_key = f"{img_path}|w{MAX_WIDTH_FOR_OCR}"
        if CACHE_ENABLED and cache_key in self._cache:
            if DEBUG_TIMINGS:
                logger.debug("Cache hit (OCR) -> instant")

            pack = self._cache[cache_key]
            self.last_output_img = pack.img_for_merge
            return pack

        # Read image with EXIF orientation correction (optimized single-load)
        try:
            from PIL import Image, ImageOps

            # Load with PIL, apply EXIF rotation automatically
            pil_img = Image.open(img_path)
            pil_img = ImageOps.exif_transpose(pil_img)  # Auto-rotate based on EXIF

            # Convert PIL → OpenCV BGR
            import numpy as np
            img_rgb = np.array(pil_img)
            if img_rgb.ndim == 2:  # Grayscale
                img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_GRAY2BGR)
            elif img_rgb.shape[2] == 3:  # RGB
                img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
            elif img_rgb.shape[2] == 4:  # RGBA
                img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGBA2BGR)
            else:
                raise ValueError(f"Format d'image non supporté: {img_rgb.shape}")

        except Exception as e:
            if DEBUG_TIMINGS:
                logger.warning(f"⚠️ EXIF rotation with PIL failed ({e}), fallback to OpenCV")
            # Fallback: load with OpenCV (no EXIF support)
            img_bgr = cv2.imread(img_path, cv2.IMREAD_COLOR)
            if img_bgr is None:
                raise RuntimeError(f"Impossible de lire l'image: {img_path}")

        t_read = time.perf_counter()

        # Resize for speed
        img_for_ocr, scale = _resize_for_ocr(img_bgr, MAX_WIDTH_FOR_OCR)
        scale_to_orig = 1.0 / float(scale) if scale != 0 else 1.0

        t_resize = time.perf_counter()

        # Initialize EasyOCR
        self._ensure_reader(lang_code)

        # Run EasyOCR
        # readtext returns: List[Tuple[bbox, text, confidence]]
        # bbox is [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
        assert self.reader is not None, "Reader should be initialized"
        raw_results = self.reader.readtext(img_for_ocr)

        t_ocr = time.perf_counter()

        # Parse EasyOCR results
        results: List[Result] = []
        for bbox, text, conf in raw_results:
            if not text.strip():
                continue

            # Convert bbox to our format
            box: Box = []
            for point in bbox:
                box.append([int(point[0]), int(point[1])])

            results.append((text, float(conf), box))

        t_parse = time.perf_counter()

        # ✅ Image for display = img_for_ocr (no transformations from EasyOCR)
        img_for_merge = img_for_ocr

        pack = OcrPack(
            img_for_merge=img_for_merge,
            results=results,
            orig_img=img_bgr,
            scale_to_orig=scale_to_orig,
        )

        if CACHE_ENABLED:
            # LRU eviction: remove oldest if cache full
            if len(self._cache) >= CACHE_MAX_SIZE:
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
            self._cache[cache_key] = pack

        if DEBUG_TIMINGS:
            logger.debug(
                f"Timings: read={int((t_read - t0)*1000)}ms | "
                f"resize={int((t_resize - t_read)*1000)}ms | "
                f"ocr={(t_ocr - t_resize):.2f}s | "
                f"parse={int((t_parse - t_ocr)*1000)}ms | "
                f"total={(t_parse - t0):.2f}s | "
                f"scale={scale:.3f}"
            )

        return pack

    def run(self, img_path: str, lang_code: str) -> List[Result]:
        pack = self._run_raw_ocr(img_path, lang_code)

        # Merge level 2 (bubbles)
        t0 = time.perf_counter()
        merged = _merge_by_bubbles(pack.img_for_merge, pack.results)
        t1 = time.perf_counter()

        if DEBUG_TIMINGS:
            logger.debug(f"fusion={int((t1 - t0)*1000)}ms | lines={len(pack.results)} -> merged={len(merged)}")

        # Show the OCR image directly so boxes stay aligned 1:1
        self.last_output_img = pack.img_for_merge

        return merged
