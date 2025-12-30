# -*- coding: utf-8 -*-
"""Worker for OCR and translation operations"""
from __future__ import annotations

from typing import List, Tuple

from PySide6.QtCore import QObject, Signal

from app.services.ocr_service import OCRService
from app.services.translate_service import TranslateService, TranslatorMode


# (text, conf, box)
OcrResult = Tuple[str, float, list]


class OCRTranslateWorker(QObject):
    """Background worker for OCR and translation"""
    finished = Signal(list, list)  # ocr_results, translations
    error = Signal(str)
    progress = Signal(int)

    def __init__(
        self,
        image_path: str,
        lang_code: str,
        ocr_service: OCRService,
        translate_service: TranslateService,
        translate_mode: TranslatorMode,
        api_key: str,
        src_lang_ui: str,
        tgt_lang_ui: str,
        auto_fallback: bool,
    ):
        super().__init__()
        self.image_path = image_path
        self.lang_code = lang_code
        self.ocr_service = ocr_service
        self.translate_service = translate_service
        self.translate_mode = translate_mode
        self.api_key = api_key
        self.src_lang_ui = src_lang_ui
        self.tgt_lang_ui = tgt_lang_ui
        self.auto_fallback = auto_fallback

    def run(self):
        """Execute OCR and translation"""
        try:
            self.progress.emit(5)

            # 1) OCR
            ocr_results: List[OcrResult] = self.ocr_service.run(self.image_path, self.lang_code)
            self.progress.emit(70)

            # 2) Translation
            texts = [t for (t, _c, _b) in ocr_results]
            translations: List[str] = []

            if texts:
                mode_txt = (self.translate_mode or "").strip().lower()
                mode: TranslatorMode = "online" if mode_txt == "online" else "local"

                self.translate_service.set_settings(
                    mode=mode,
                    api_key=self.api_key,
                    src_lang=self.src_lang_ui,
                    tgt_lang=self.tgt_lang_ui,
                    auto_fallback_to_local=bool(self.auto_fallback and (self.api_key or "").strip()),
                    provider="deepl",
                )

                translations = self.translate_service.translate_many(texts)

            self.progress.emit(100)
            self.finished.emit(ocr_results, translations)

        except Exception as e:
            self.error.emit(str(e))
