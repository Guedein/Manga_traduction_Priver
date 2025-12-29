# app/services/export_service.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


class ExportService:
    """Service d'export (image + PDF)"""

    def export_image(self, img_bgr: np.ndarray, output_path: str) -> None:
        """
        Exporte une image traduite en PNG/JPG.

        Args:
            img_bgr: Image BGR (OpenCV)
            output_path: Chemin de sortie (ex: "output/page_001.png")
        """
        # Créer le dossier parent si nécessaire
        output_dir = Path(output_path).parent
        output_dir.mkdir(parents=True, exist_ok=True)

        # Sauvegarder l'image
        success = cv2.imwrite(output_path, img_bgr)
        if not success:
            raise RuntimeError(f"Échec de l'export image : {output_path}")

    def export_pdf(self, img_bgr: np.ndarray, output_path: str) -> None:
        """
        Exporte une image traduite en PDF (1 page).

        Args:
            img_bgr: Image BGR (OpenCV)
            output_path: Chemin de sortie (ex: "output/chapter_01.pdf")
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

        # Créer le dossier parent si nécessaire
        output_dir = Path(output_path).parent
        output_dir.mkdir(parents=True, exist_ok=True)

        # Convertir BGR → RGB pour PIL
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)

        # Dimensions de l'image
        img_w, img_h = pil_img.size

        # Calculer taille de page pour conserver aspect ratio
        # On utilise A4 comme référence, mais on adapte pour garder l'aspect ratio
        a4_w, a4_h = A4

        # Scale pour FIT l'image dans A4
        scale = min(a4_w / img_w, a4_h / img_h)
        page_w = img_w * scale
        page_h = img_h * scale

        # Créer PDF
        c = canvas.Canvas(output_path, pagesize=(page_w, page_h))

        # Dessiner l'image sur toute la page
        img_reader = ImageReader(pil_img)
        c.drawImage(img_reader, 0, 0, width=page_w, height=page_h)

        # Sauvegarder
        c.save()

    def export_both(
        self,
        img_bgr: np.ndarray,
        output_dir: str,
        base_name: str = "page_001",
        image_format: str = "png"
    ) -> tuple[str, str]:
        """
        Exporte à la fois l'image et le PDF.

        Args:
            img_bgr: Image BGR (OpenCV)
            output_dir: Dossier de sortie
            base_name: Nom de base (ex: "page_001")
            image_format: Format d'image ("png" ou "jpg")

        Returns:
            (chemin_image, chemin_pdf)
        """
        # Chemins de sortie
        img_path = os.path.join(output_dir, f"{base_name}.{image_format}")
        pdf_path = os.path.join(output_dir, f"{base_name}.pdf")

        # Export
        self.export_image(img_bgr, img_path)
        self.export_pdf(img_bgr, pdf_path)

        return img_path, pdf_path
