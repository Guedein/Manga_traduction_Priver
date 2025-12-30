# -*- coding: utf-8 -*-
"""Worker for downloading chapters from URLs"""
from __future__ import annotations

from typing import List

from PySide6.QtCore import QObject, Signal

from app.services.download_service import DownloadService


class DownloadWorker(QObject):
    """Background worker for downloading a chapter from URL"""
    finished = Signal(str, str, list)  # (manga_name, chapter_name, list_of_image_paths)
    error = Signal(str)
    progress = Signal(int, int, str)  # (current, total, status_text)

    def __init__(
        self,
        chapter_url: str,
        base_output_dir: str,
        download_service: DownloadService,
        auto_process: bool = False,
    ):
        super().__init__()
        self.chapter_url = chapter_url
        self.base_output_dir = base_output_dir
        self.download_service = download_service
        self.auto_process = auto_process

    def run(self):
        """Download chapter images"""
        try:
            manga_name, chapter_name, downloaded_files = self.download_service.download_chapter(
                self.chapter_url,
                self.base_output_dir,
                progress_callback=lambda i, t, s: self.progress.emit(i, t, s)
            )
            self.finished.emit(manga_name, chapter_name, downloaded_files)
        except Exception as e:
            self.error.emit(str(e))
