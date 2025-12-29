# app/services/render_service.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple, Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


# ---------------------------
# Configuration
# ---------------------------

@dataclass
class RenderConfig:
    """Configuration pour le rendu du texte"""
    # Inpainting
    inpaint_radius: int = 15  # Rayon d'inpainting (pixels) - augmenté pour meilleure reconstruction

    # Marges internes (% de la largeur/hauteur de la box)
    margin_horizontal: float = 0.10  # 10% de marge horizontale
    margin_vertical: float = 0.10    # 10% de marge verticale

    # Texte
    font_path: Optional[str] = None  # Chemin vers police .ttf (None = police par d�faut)
    font_color: Tuple[int, int, int] = (0, 0, 0)  # Noir par d�faut (RGB)
    max_font_size: int = 100  # Taille max de police
    min_font_size: int = 8    # Taille min de police
    line_spacing: float = 1.2  # Espacement entre lignes (multiplicateur)


Box = List[List[int]]  # [[x,y], [x,y], [x,y], [x,y]]


# ---------------------------
# Utilitaires g�om�triques
# ---------------------------

def _poly_to_aabb(poly: Box) -> Tuple[int, int, int, int]:
    """Convertit un polygone en bounding box (x1, y1, x2, y2)"""
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))


# ---------------------------
# Inpainting
# ---------------------------

def _create_mask_from_box(img_shape: Tuple[int, int], box: Box) -> np.ndarray:
    """
    Cr�e un masque binaire pour la zone de la box.

    Args:
        img_shape: (height, width) de l'image
        box: Polygone de la box

    Returns:
        Masque binaire (255 = zone � inpainter, 0 = reste)
    """
    h, w = img_shape
    mask = np.zeros((h, w), dtype=np.uint8)

    # Convertir box en numpy array pour fillPoly
    pts = np.array(box, dtype=np.int32)
    cv2.fillPoly(mask, [pts], 255)

    return mask


def inpaint_text(img_bgr: np.ndarray, box: Box, config: RenderConfig) -> np.ndarray:
    """
    Efface le texte dans la box en utilisant l'inpainting OpenCV.

    Args:
        img_bgr: Image BGR (OpenCV)
        box: Polygone de la box contenant le texte
        config: Configuration de rendu

    Returns:
        Image avec texte effac�
    """
    h, w = img_bgr.shape[:2]

    # Cr�er masque pour la zone � inpainter
    mask = _create_mask_from_box((h, w), box)

    # Inpainting (reconstruction du fond)
    # cv2.INPAINT_TELEA : m�thode rapide et efficace
    inpainted = cv2.inpaint(
        img_bgr,
        mask,
        inpaintRadius=config.inpaint_radius,
        flags=cv2.INPAINT_TELEA
    )

    return inpainted


# ---------------------------
# Rendu texte
# ---------------------------

def _get_default_font(size: int):
    """Retourne une police par d�faut"""
    try:
        # Essayer police system (Windows)
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        try:
            # Fallback : DejaVu (Linux/cross-platform)
            return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
        except Exception:
            # Derni�re option : police bitmap par d�faut
            return ImageFont.load_default()


def _load_font(font_path: Optional[str], size: int):
    """Charge une police TrueType"""
    if font_path:
        try:
            return ImageFont.truetype(font_path, size)
        except Exception as e:
            print(f"� Impossible de charger la police {font_path}: {e}")
            print("   � Utilisation de la police par d�faut")

    return _get_default_font(size)


def _wrap_text(text: str, font, max_width: int) -> List[str]:
    """
    D�coupe le texte en lignes pour qu'il tienne dans max_width.

    Args:
        text: Texte � d�couper
        font: Police utilis�e
        max_width: Largeur maximale en pixels

    Returns:
        Liste de lignes
    """
    words = text.split()
    if not words:
        return []

    lines: List[str] = []
    current_line = words[0]

    for word in words[1:]:
        # Tester si on peut ajouter le mot � la ligne courante
        test_line = f"{current_line} {word}"
        bbox = font.getbbox(test_line)
        width = bbox[2] - bbox[0]

        if width <= max_width:
            current_line = test_line
        else:
            # Ligne trop longue, cr�er nouvelle ligne
            lines.append(current_line)
            current_line = word

    # Ajouter derni�re ligne
    lines.append(current_line)

    return lines


def _find_optimal_font_size(
    text: str,
    box_width: int,
    box_height: int,
    font_path: Optional[str],
    config: RenderConfig
):
    """
    Trouve la taille de police optimale pour que le texte tienne dans la box.

    Returns:
        (font, lignes_wrapped, font_size)
    """
    # Dichotomie sur la taille de police
    min_size = config.min_font_size
    max_size = config.max_font_size
    best_font = _load_font(font_path, min_size)
    best_lines = [text]
    best_size = min_size

    for font_size in range(max_size, min_size - 1, -1):
        font = _load_font(font_path, font_size)
        lines = _wrap_text(text, font, box_width)

        # Calculer hauteur totale
        line_height = font.getbbox("Ay")[3] - font.getbbox("Ay")[1]
        total_height = int(line_height * len(lines) * config.line_spacing)

        # V�rifier si �a rentre
        if total_height <= box_height:
            # V�rifier largeur de chaque ligne
            fits = True
            for line in lines:
                bbox = font.getbbox(line)
                line_width = bbox[2] - bbox[0]
                if line_width > box_width:
                    fits = False
                    break

            if fits:
                return font, lines, font_size

    # Si rien ne rentre, retourner taille min
    return best_font, best_lines, best_size


def render_text_in_box(
    img_bgr: np.ndarray,
    text: str,
    box: Box,
    config: RenderConfig
) -> np.ndarray:
    """
    Dessine le texte traduit dans la box.

    Args:
        img_bgr: Image BGR (OpenCV)
        text: Texte � dessiner
        box: Polygone de la box
        config: Configuration de rendu

    Returns:
        Image avec texte dessin�
    """
    if not text.strip():
        return img_bgr

    # Convertir BGR � RGB pour Pillow
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(img_rgb)
    draw = ImageDraw.Draw(pil_img)

    # Calculer zone de texte avec marges
    x1, y1, x2, y2 = _poly_to_aabb(box)
    box_w = x2 - x1
    box_h = y2 - y1

    margin_x = int(box_w * config.margin_horizontal)
    margin_y = int(box_h * config.margin_vertical)

    text_w = box_w - 2 * margin_x
    text_h = box_h - 2 * margin_y

    if text_w <= 0 or text_h <= 0:
        print(f"� Box trop petite pour le texte : {box_w}x{box_h}")
        return img_bgr

    # Trouver taille de police optimale
    font, lines, font_size = _find_optimal_font_size(
        text, text_w, text_h, config.font_path, config
    )

    # Calculer position Y de d�part (centrage vertical)
    line_height = font.getbbox("Ay")[3] - font.getbbox("Ay")[1]
    total_text_height = int(line_height * len(lines) * config.line_spacing)
    y_start = y1 + margin_y + (text_h - total_text_height) // 2

    # Dessiner chaque ligne (centrage horizontal)
    y_current = y_start
    for line in lines:
        bbox = font.getbbox(line)
        line_width = bbox[2] - bbox[0]

        # Centrage horizontal
        x_pos = x1 + margin_x + (text_w - line_width) // 2
        y_pos = y_current

        # Dessiner texte
        draw.text((x_pos, y_pos), line, font=font, fill=config.font_color)

        y_current += int(line_height * config.line_spacing)

    # Convertir RGB � BGR pour OpenCV
    img_rgb_result = np.array(pil_img)
    img_bgr_result = cv2.cvtColor(img_rgb_result, cv2.COLOR_RGB2BGR)

    return img_bgr_result


# ---------------------------
# RenderService (API principale)
# ---------------------------

class RenderService:
    """Service de rendu (inpainting + texte)"""

    def __init__(self, config: Optional[RenderConfig] = None):
        self.config = config or RenderConfig()

    def set_config(self, config: RenderConfig) -> None:
        """Met � jour la configuration"""
        self.config = config

    def render_translated_image(
        self,
        img_bgr: np.ndarray,
        boxes: List[Box],
        translations: List[str]
    ) -> np.ndarray:
        """
        Traite une image compl�te : inpainting + rendu texte.

        Args:
            img_bgr: Image BGR (OpenCV)
            boxes: Liste des boxes de texte
            translations: Textes traduits (m�me ordre que boxes)

        Returns:
            Image finale avec texte traduit
        """
        if len(boxes) != len(translations):
            raise ValueError(f"Nombre de boxes ({len(boxes)}) != nombre de traductions ({len(translations)})")

        # Copie pour ne pas modifier l'original
        result = img_bgr.copy()

        # �tape 1 : Inpainting (effacer tout le texte)
        for box in boxes:
            result = inpaint_text(result, box, self.config)

        # �tape 2 : Dessiner texte traduit
        for box, text in zip(boxes, translations):
            result = render_text_in_box(result, text, box, self.config)

        return result
