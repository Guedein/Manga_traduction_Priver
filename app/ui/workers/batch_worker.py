# -*- coding: utf-8 -*-
"""Worker for batch image processing"""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np
from PySide6.QtCore import QObject, Signal

from app.services.ocr_service import OCRService
from app.services.translate_service import TranslateService
from app.services.render_service import RenderService
from app.services.batch_service import BatchService


class BatchWorker(QObject):
    """Background worker for batch processing a folder of images"""
    finished = Signal(list, str)  # (list_of_image_paths, pdf_path)
    error = Signal(str)
    progress = Signal(int, int, str)  # (current, total, status_text)

    def __init__(
        self,
        folder_path: str,
        output_dir: str,
        chapter_name: str,
        create_pdf: bool,
        lang_code: str,
        ocr_service: OCRService,
        translate_service: TranslateService,
        render_service: RenderService,
        batch_service: BatchService,
    ):
        super().__init__()
        self.folder_path = folder_path
        self.output_dir = output_dir
        self.chapter_name = chapter_name
        self.create_pdf = create_pdf
        self.lang_code = lang_code
        self.ocr_service = ocr_service
        self.translate_service = translate_service
        self.render_service = render_service
        self.batch_service = batch_service

    def run(self):
        """Process all images in folder"""
        try:
            # 1) Scan folder
            self.progress.emit(0, 100, "Scan du dossier...")
            image_paths = self.batch_service.scan_folder(self.folder_path)

            if not image_paths:
                self.error.emit(f"Aucune image trouvée dans : {self.folder_path}")
                return

            total_images = len(image_paths)
            self.progress.emit(0, total_images, f"Trouvé {total_images} images")

            # 2) Process each image (OCR + Translation + Render)
            rendered_images: List[Tuple[str, np.ndarray]] = []

            for idx, img_path in enumerate(image_paths, start=1):
                img_name = Path(img_path).name
                self.progress.emit(idx, total_images, f"Traitement {img_name} ({idx}/{total_images})")

                try:
                    # OCR
                    ocr_results = self.ocr_service.run(img_path, self.lang_code)

                    # Translation
                    texts = [t for (t, _c, _b) in ocr_results]
                    translations = self.translate_service.translate_many(texts) if texts else []

                    # Render
                    img_bgr = cv2.imread(img_path)
                    if img_bgr is None:
                        raise RuntimeError(f"Impossible de charger l'image : {img_path}")

                    boxes = [b for (_t, _c, b) in ocr_results]
                    rendered_img = self.render_service.render_translated_image(img_bgr, boxes, translations)

                    rendered_images.append((img_name, rendered_img))

                except Exception as e:
                    # Continue even if one image fails
                    self.progress.emit(idx, total_images, f"⚠ Erreur sur {img_name}: {str(e)[:50]}")
                    continue

            if not rendered_images:
                self.error.emit("Aucune image n'a pu être traitée avec succès")
                return

            # 3) Export batch
            self.progress.emit(total_images, total_images, "Export des images...")

            exported_imgs, pdf_path = self.batch_service.export_batch(
                rendered_images=rendered_images,
                output_dir=self.output_dir,
                chapter_name=self.chapter_name,
                create_pdf=self.create_pdf,
                progress_callback=lambda i, t, s: self.progress.emit(i, t, s)
            )

            self.finished.emit(exported_imgs, pdf_path or "")

        except Exception as e:
            self.error.emit(str(e))
