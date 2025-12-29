# app/services/batch_service.py
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Tuple, Optional, Callable, Set

import cv2
import numpy as np


class BatchService:
    """Service de traitement batch pour dossiers d'images"""

    # Extensions d'images supportées
    SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}

    def __init__(self):
        pass

    def scan_folder(self, folder_path: str) -> List[str]:
        """
        Scanne un dossier et retourne la liste triée des images.

        Args:
            folder_path: Chemin vers le dossier à scanner

        Returns:
            Liste triée des chemins d'images
        """
        folder = Path(folder_path)
        if not folder.exists() or not folder.is_dir():
            raise ValueError(f"Dossier invalide : {folder_path}")

        # Trouver toutes les images (utiliser un set pour éviter les doublons sur Windows)
        images_set: Set[Path] = set()
        for ext in self.SUPPORTED_EXTENSIONS:
            images_set.update(folder.glob(f"*{ext}"))
            images_set.update(folder.glob(f"*{ext.upper()}"))

        # Trier par nom (ordre naturel des chapitres)
        images_sorted = sorted(images_set, key=lambda p: p.name.lower())

        return [str(p) for p in images_sorted]

    def create_multi_page_pdf(
        self,
        image_paths: List[str],
        output_pdf_path: str,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> None:
        """
        Crée un PDF multi-pages à partir d'une liste d'images.

        Args:
            image_paths: Liste des chemins d'images (dans l'ordre)
            output_pdf_path: Chemin du PDF de sortie
            progress_callback: Callback optionnel (page_actuelle, total_pages)
        """
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.utils import ImageReader
            from reportlab.pdfgen import canvas
            from PIL import Image
        except ImportError:
            raise RuntimeError(
                "ReportLab n'est pas installé. Installez-le avec: pip install reportlab"
            )

        if not image_paths:
            raise ValueError("Aucune image à exporter en PDF")

        # Créer le dossier parent si nécessaire
        output_dir = Path(output_pdf_path).parent
        output_dir.mkdir(parents=True, exist_ok=True)

        # Créer le PDF
        c = canvas.Canvas(output_pdf_path)

        for idx, img_path in enumerate(image_paths, start=1):
            if progress_callback:
                progress_callback(idx, len(image_paths))

            # Charger l'image
            if isinstance(img_path, str) and img_path.endswith(('.png', '.jpg', '.jpeg')):
                # Si c'est un chemin de fichier
                if os.path.exists(img_path):
                    pil_img = Image.open(img_path)
                else:
                    # Si c'est un array numpy (image déjà en mémoire)
                    continue
            else:
                continue

            # Dimensions de l'image
            img_w, img_h = pil_img.size

            # Calculer taille de page pour conserver aspect ratio
            a4_w, a4_h = A4

            # Scale pour FIT l'image dans A4
            scale = min(a4_w / img_w, a4_h / img_h)
            page_w = img_w * scale
            page_h = img_h * scale

            # Définir la taille de la page
            c.setPageSize((page_w, page_h))

            # Dessiner l'image sur toute la page
            img_reader = ImageReader(pil_img)
            c.drawImage(img_reader, 0, 0, width=page_w, height=page_h)

            # Page suivante (sauf pour la dernière)
            if idx < len(image_paths):
                c.showPage()

        # Sauvegarder le PDF
        c.save()

    def export_batch(
        self,
        rendered_images: List[Tuple[str, np.ndarray]],  # (nom_fichier, image_bgr)
        output_dir: str,
        chapter_name: str = "chapter",
        create_pdf: bool = True,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> Tuple[List[str], Optional[str]]:
        """
        Exporte un batch d'images traduites (images + PDF optionnel).

        Args:
            rendered_images: Liste de (nom_fichier, image_bgr)
            output_dir: Dossier de sortie
            chapter_name: Nom du chapitre (pour le PDF)
            create_pdf: Créer un PDF multi-pages
            progress_callback: Callback (index, total, status_text)

        Returns:
            (liste_chemins_images, chemin_pdf)
        """
        if not rendered_images:
            raise ValueError("Aucune image à exporter")

        # Créer le dossier de sortie
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        # Export des images
        exported_images: List[str] = []

        for idx, (original_name, img_bgr) in enumerate(rendered_images, start=1):
            if progress_callback:
                progress_callback(idx, len(rendered_images), f"Export image {idx}/{len(rendered_images)}")

            # Nom de fichier avec padding (ex: page_001.png)
            base_name = Path(original_name).stem
            img_filename = f"{base_name}_traduit.png"
            img_path = out_path / img_filename

            # Sauvegarder l'image
            cv2.imwrite(str(img_path), img_bgr)
            exported_images.append(str(img_path))

        # Créer PDF multi-pages
        pdf_path = None
        if create_pdf:
            if progress_callback:
                progress_callback(
                    len(rendered_images),
                    len(rendered_images),
                    "Création du PDF multi-pages..."
                )

            pdf_filename = f"{chapter_name}.pdf"
            pdf_path = str(out_path / pdf_filename)

            self.create_multi_page_pdf(
                exported_images,
                pdf_path,
                progress_callback=None  # Pas de sous-callback pour simplifier
            )

        return exported_images, pdf_path
