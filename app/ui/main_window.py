# -*- coding: utf-8 -*-
# app/ui/main_window_v2.py
"""
Version 2 de MainWindow avec interface à onglets :
- Onglet 1 : Traitement Local (images du PC)
- Onglet 2 : Téléchargement URL
- Onglet 3 : Développeur (protégé par mot de passe)
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, List, Tuple, cast
import json

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QPushButton, QComboBox, QProgressBar, QLabel,
    QFileDialog, QHBoxLayout, QVBoxLayout, QSplitter, QGroupBox, QLineEdit, QCheckBox,
    QTabWidget, QInputDialog, QMessageBox, QTextEdit
)

from app.ui.widgets.image_viewer import ImageViewer
from app.ui.widgets.log_panel import LogPanel
from app.ui.workers import OCRTranslateWorker, BatchWorker, DownloadWorker
from app.services.ocr_service import OCRService
from app.services.translate_service import TranslateService
from app.services.translate_service import TranslatorMode
from app.services.render_service import RenderService
from app.services.export_service import ExportService
from app.services.batch_service import BatchService
from app.services.download_service import DownloadService

import numpy as np


# (text, conf, box)
OcrResult = Tuple[str, float, list]


class OCRTranslateWorkerOld(QObject):
    """Worker pour OCR + Traduction d'une image (ancien code, gardé pour référence)"""
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
        try:
            self.progress.emit(5)

            # 1) OCR
            ocr_results: List[OcrResult] = self.ocr_service.run(self.image_path, self.lang_code)
            self.progress.emit(70)

            # 2) Traduction
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


class BatchWorker(QObject):
    """Worker pour traiter un dossier complet d'images"""
    finished = Signal(list, str)  # (liste_chemins_images, chemin_pdf)
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
        try:
            # 1) Scanner le dossier
            self.progress.emit(0, 100, "Scan du dossier...")
            image_paths = self.batch_service.scan_folder(self.folder_path)

            if not image_paths:
                self.error.emit(f"Aucune image trouvée dans : {self.folder_path}")
                return

            total_images = len(image_paths)
            self.progress.emit(0, total_images, f"Trouvé {total_images} images")

            # 2) Traiter chaque image (OCR + Traduction + Rendu)
            rendered_images: List[Tuple[str, np.ndarray]] = []

            for idx, img_path in enumerate(image_paths, start=1):
                img_name = Path(img_path).name
                self.progress.emit(idx, total_images, f"Traitement {img_name} ({idx}/{total_images})")

                try:
                    # OCR
                    ocr_results = self.ocr_service.run(img_path, self.lang_code)

                    # Traduction
                    texts = [t for (t, _c, _b) in ocr_results]
                    translations = self.translate_service.translate_many(texts) if texts else []

                    # Rendu
                    import cv2
                    img_bgr = cv2.imread(img_path)
                    if img_bgr is None:
                        raise RuntimeError(f"Impossible de charger l'image : {img_path}")

                    boxes = [b for (_t, _c, b) in ocr_results]
                    rendered_img = self.render_service.render_translated_image(img_bgr, boxes, translations)

                    rendered_images.append((img_name, rendered_img))

                except Exception as e:
                    # Continuer même si une image échoue
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


class DownloadWorker(QObject):
    """Worker pour télécharger un chapitre depuis une URL"""
    finished = Signal(str, str, list)  # (manga_name, chapter_name, liste_chemins_images)
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
        self.stop_requested = False  # Flag pour arrêter le téléchargement

    def stop(self):
        """Demande l'arrêt du téléchargement"""
        self.stop_requested = True

    def run(self):
        try:
            # Callback avec vérification du stop
            def progress_with_stop(i, t, s):
                if self.stop_requested:
                    raise InterruptedError("Téléchargement arrêté par l'utilisateur")
                self.progress.emit(i, t, s)

            manga_name, chapter_name, downloaded_files = self.download_service.download_chapter(
                self.chapter_url,
                self.base_output_dir,
                progress_callback=progress_with_stop
            )

            if not self.stop_requested:
                self.finished.emit(manga_name, chapter_name, downloaded_files)
        except InterruptedError as e:
            self.error.emit(str(e))
        except Exception as e:
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Manga Translator Pro")
        self.resize(1200, 800)

        # Charger la configuration
        self.config = self._load_config()

        # Variables d'état
        self.current_image_path: Optional[str] = None
        self.last_ocr_results: List[OcrResult] = []
        self.last_translations: List[str] = []
        self.last_rendered_img: Optional[np.ndarray] = None
        self.last_export_dir: Optional[str] = None
        self.batch_folder_path: Optional[str] = None
        self.dev_unlocked = False

        # Services
        self.ocr_service = OCRService()
        self.translate_service = TranslateService()
        self.render_service = RenderService()
        self.export_service = ExportService()
        self.batch_service = BatchService()
        self.download_service = DownloadService()

        # Threads
        self._thread: Optional[QThread] = None
        self._worker: Optional[OCRTranslateWorker] = None
        self._batch_thread: Optional[QThread] = None
        self._batch_worker: Optional[BatchWorker] = None
        self._download_thread: Optional[QThread] = None
        self._download_worker: Optional[DownloadWorker] = None

        # Créer l'interface
        self._create_ui()

    def _load_config(self) -> dict:
        """Charge la configuration depuis config.json"""
        config_path = Path(__file__).parent.parent.parent / "config.json"
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Erreur lors du chargement de config.json : {e}")
            # Configuration par défaut
            return {
                "security": {"dev_password": "dev123"},
                "api_keys": {"deepl_api_key": ""},
                "selenium": {"headless": True, "timeout": 30},
                "paths": {"default_export_dir": ""}
            }

    def _create_ui(self):
        """Crée l'interface principale avec onglets"""
        root = QWidget()
        self.setCentralWidget(root)

        # Système d'onglets
        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Créer les onglets
        self._create_local_tab()
        self._create_url_tab()
        self._create_dev_tab()

        # Layout principal
        main_layout = QVBoxLayout(root)
        main_layout.addWidget(self.tabs)

    def _create_local_tab(self):
        """Onglet 1: Traitement Local (images du PC)"""
        tab = QWidget()

        # Widgets
        btn_choose = QPushButton("🖼️ Choisir une image")
        btn_run = QPushButton("🚀 Lancer OCR + Traduction")
        btn_run.setEnabled(False)
        btn_render = QPushButton("🎨 Appliquer traduction")
        btn_render.setEnabled(False)

        btn_choose_folder = QPushButton("📂 Choisir un dossier")
        btn_run_batch = QPushButton("▶ Traiter dossier complet")
        btn_run_batch.setEnabled(False)

        btn_set_export_dir = QPushButton("📁 Dossier d'export...")
        btn_export = QPushButton("💾 Exporter")
        btn_export.setEnabled(False)

        # Sauvegarde des références
        self.btn_choose = btn_choose
        self.btn_run = btn_run
        self.btn_render = btn_render
        self.btn_choose_folder = btn_choose_folder
        self.btn_run_batch = btn_run_batch
        self.btn_set_export_dir = btn_set_export_dir
        self.btn_export = btn_export

        # Configuration traduction
        self.lang_combo = QComboBox()
        self.lang_map = {
            "Auto": "auto",
            "EN": "en",
            "CH": "ch",
            "JP": "jp",
            "KR": "kr",
        }
        self.lang_combo.addItems(list(self.lang_map.keys()))

        self.translate_mode_combo = QComboBox()
        self.translate_mode_combo.addItems(["Online (API)", "Local (offline)"])

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setPlaceholderText("Clé API DeepL")
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)

        self.fallback_chk = QCheckBox("Fallback auto Online → Local")
        self.fallback_chk.setChecked(True)

        self.tgt_lang_ui = "FR"

        # Progress bar
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        # Preview + Logs
        self.image_viewer = ImageViewer()
        self.logs = LogPanel()

        # Layouts
        # Ligne 1 : Une seule image
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Image unique :"))
        row1.addWidget(btn_choose)
        row1.addWidget(btn_run)
        row1.addWidget(btn_render)
        row1.addStretch()

        # Ligne 2 : Dossier complet
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Dossier complet :"))
        row2.addWidget(btn_choose_folder)
        row2.addWidget(btn_run_batch)
        row2.addStretch()

        # Ligne 3 : Export
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Export :"))
        row3.addWidget(btn_set_export_dir)
        row3.addWidget(btn_export)
        row3.addStretch()

        # Ligne 4 : Configuration
        row4 = QHBoxLayout()
        row4.addWidget(QLabel("Langue source :"))
        row4.addWidget(self.lang_combo)
        row4.addSpacing(20)
        row4.addWidget(QLabel("Traduction :"))
        row4.addWidget(self.translate_mode_combo)
        row4.addWidget(self.api_key_edit)
        row4.addWidget(self.fallback_chk)
        row4.addStretch()

        # Preview + Logs
        preview_group = QGroupBox("Aperçu de l'image")
        preview_layout = QVBoxLayout(preview_group)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addWidget(self.image_viewer)

        logs_group = QGroupBox("Journal d'activité")
        logs_layout = QVBoxLayout(logs_group)
        logs_layout.setContentsMargins(0, 0, 0, 0)
        logs_layout.addWidget(self.logs)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(preview_group)
        splitter.addWidget(logs_group)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([800, 300])

        # Layout principal
        layout = QVBoxLayout(tab)
        layout.addLayout(row1)
        layout.addLayout(row2)
        layout.addLayout(row3)
        layout.addLayout(row4)
        layout.addWidget(self.progress)
        layout.addWidget(splitter, 1)

        # Connecter les signaux
        btn_choose.clicked.connect(self.on_choose_image)
        btn_run.clicked.connect(self.on_run_ocr)
        btn_render.clicked.connect(self.on_render_translated)
        btn_choose_folder.clicked.connect(self.on_choose_folder)
        btn_run_batch.clicked.connect(self.on_run_batch)
        btn_set_export_dir.clicked.connect(self.on_set_export_dir)
        btn_export.clicked.connect(self.on_export)
        self.translate_mode_combo.currentIndexChanged.connect(self.on_translate_mode_changed)

        # Ajouter l'onglet
        self.tabs.addTab(tab, "🖼️ Traitement Local")

        # Message initial
        self.logs.log("✅ Manga Translator Pro démarré")
        self.logs.log("📌 Onglet 'Traitement Local' : Pour traiter des images depuis votre PC")
        self.on_translate_mode_changed(0)

    def _create_url_tab(self):
        """Onglet 2: Téléchargement depuis URL"""
        tab = QWidget()

        # Widgets
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("URL du chapitre (ex: https://manhuaus.com/manga/...)")

        btn_download = QPushButton("⬇ Télécharger seulement")
        btn_download_and_process = QPushButton("⬇ Télécharger + Traiter")
        btn_stop_download = QPushButton("⏹️ Arrêter")
        btn_stop_download.setEnabled(False)
        btn_stop_download.setStyleSheet("QPushButton:enabled { background-color: #d32f2f; color: white; }")

        self.btn_download = btn_download
        self.btn_download_and_process = btn_download_and_process
        self.btn_stop_download = btn_stop_download

        # Configuration - Langue source
        self.url_lang_combo = QComboBox()
        self.url_lang_combo.addItems(["Auto", "EN", "CH", "JP", "KR"])

        # Configuration - Mode de traduction
        self.url_translate_mode_combo = QComboBox()
        self.url_translate_mode_combo.addItems(["Online (API)", "Local (offline)"])

        # Configuration - Clé API
        self.url_api_key_edit = QLineEdit()
        self.url_api_key_edit.setPlaceholderText("Clé API DeepL")
        self.url_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        # Charger la clé API depuis la config
        saved_api_key = self.config.get("api_keys", {}).get("deepl_api_key", "")
        if saved_api_key:
            self.url_api_key_edit.setText(saved_api_key)

        # Configuration - Dossier d'export
        self.url_export_dir_label = QLabel("📁 Dossier d'export : Non configuré")
        btn_set_url_export_dir = QPushButton("📁 Choisir dossier d'export")

        # Fallback
        self.url_fallback_chk = QCheckBox("Fallback auto Online → Local")
        self.url_fallback_chk.setChecked(True)

        # Progress bar
        self.url_progress = QProgressBar()
        self.url_progress.setRange(0, 100)
        self.url_progress.setValue(0)

        # Logs
        self.url_logs = LogPanel()

        # Info
        info_label = QLabel(
            "<b>Instructions :</b><br>"
            "1. Configurez le dossier d'export dans l'onglet 'Traitement Local'<br>"
            "2. Collez l'URL du chapitre ci-dessous<br>"
            "3. Cliquez sur 'Télécharger' ou 'Télécharger + Traiter'"
        )
        info_label.setWordWrap(True)

        # Layout
        layout = QVBoxLayout(tab)
        layout.addWidget(info_label)
        layout.addSpacing(20)

        # Ligne 1 : URL
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("URL du chapitre :"))
        layout.addLayout(row1)
        layout.addWidget(self.url_edit)
        layout.addSpacing(10)

        # Ligne 2 : Boutons
        row2 = QHBoxLayout()
        row2.addWidget(btn_download)
        row2.addWidget(btn_download_and_process)
        row2.addWidget(btn_stop_download)
        row2.addStretch()
        layout.addLayout(row2)
        layout.addSpacing(20)

        # Ligne 3 : Configuration langue + traduction
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Langue source :"))
        row3.addWidget(self.url_lang_combo)
        row3.addSpacing(20)
        row3.addWidget(QLabel("Traduction :"))
        row3.addWidget(self.url_translate_mode_combo)
        row3.addWidget(self.url_api_key_edit)
        row3.addWidget(self.url_fallback_chk)
        row3.addStretch()
        layout.addLayout(row3)

        # Ligne 4 : Export
        row4 = QHBoxLayout()
        row4.addWidget(self.url_export_dir_label)
        row4.addWidget(btn_set_url_export_dir)
        row4.addStretch()
        layout.addLayout(row4)

        # Progress
        layout.addSpacing(10)
        layout.addWidget(self.url_progress)

        # Logs
        logs_group = QGroupBox("Journal d'activité")
        logs_layout = QVBoxLayout(logs_group)
        logs_layout.setContentsMargins(0, 0, 0, 0)
        logs_layout.addWidget(self.url_logs)
        layout.addWidget(logs_group, 1)

        # Connecter les signaux
        btn_download.clicked.connect(self.on_download)
        btn_download_and_process.clicked.connect(self.on_download_and_process)
        btn_stop_download.clicked.connect(self.on_stop_download)
        btn_set_url_export_dir.clicked.connect(self.on_set_url_export_dir)
        self.url_translate_mode_combo.currentIndexChanged.connect(self.on_url_translate_mode_changed)
        self.url_api_key_edit.textChanged.connect(self.on_url_api_key_changed)

        # Initialiser l'affichage selon le mode
        self.on_url_translate_mode_changed(0)

        # Ajouter l'onglet
        self.tabs.addTab(tab, "🌐 Téléchargement URL")

        # Message initial
        self.url_logs.log("✅ Onglet 'Téléchargement URL' prêt")
        self.url_logs.log("📌 Configurez la langue source et le mode de traduction ci-dessus")

    def _create_dev_tab(self):
        """Onglet 3: Développeur (protégé par mot de passe)"""
        tab = QWidget()

        # Zone de test
        self.dev_url_test = QLineEdit()
        self.dev_url_test.setPlaceholderText("URL de test pour extraction d'images")

        btn_test_extract = QPushButton("🧪 Tester extraction")
        self.dev_output = QTextEdit()
        self.dev_output.setReadOnly(True)
        self.dev_output.setPlaceholderText("Les résultats du test s'afficheront ici...")

        # Sauvegarder les widgets pour pouvoir les cacher/afficher
        self.dev_content = QWidget()
        content_layout = QVBoxLayout(self.dev_content)
        content_layout.addWidget(QLabel("<b>🔒 Onglet Développeur</b>"))
        content_layout.addWidget(QLabel("Zone de test pour le téléchargement et l'extraction d'images"))
        content_layout.addSpacing(20)
        content_layout.addWidget(QLabel("URL de test :"))
        content_layout.addWidget(self.dev_url_test)
        content_layout.addWidget(btn_test_extract)
        content_layout.addSpacing(10)
        content_layout.addWidget(QLabel("Résultats :"))
        content_layout.addWidget(self.dev_output)

        # Page de verrouillage
        self.dev_lock = QWidget()
        lock_layout = QVBoxLayout(self.dev_lock)
        lock_layout.addStretch()
        lock_layout.addWidget(QLabel("<h2>🔒 Onglet Développeur Verrouillé</h2>"), alignment=Qt.AlignmentFlag.AlignCenter)
        lock_layout.addSpacing(20)
        btn_unlock = QPushButton("🔓 Déverrouiller")
        btn_unlock.setMaximumWidth(200)
        btn_unlock.clicked.connect(self._unlock_dev_tab)
        lock_layout.addWidget(btn_unlock, alignment=Qt.AlignmentFlag.AlignCenter)
        lock_layout.addStretch()

        # Layout principal
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.dev_content)
        layout.addWidget(self.dev_lock)

        # Cacher le contenu, montrer le verrou
        self.dev_content.hide()
        self.dev_lock.show()

        # Connecter signal
        btn_test_extract.clicked.connect(self.on_dev_test_extract)

        # Ajouter l'onglet (activé, mais avec page de verrouillage)
        self.tabs.addTab(tab, "🔧 Développeur")

    def _on_tab_changed(self, index: int):
        """Callback quand on change d'onglet"""
        pass  # Plus utilisé, la logique est dans _unlock_dev_tab

    def _unlock_dev_tab(self):
        """Déverrouille l'onglet développeur avec mot de passe"""
        password, ok = QInputDialog.getText(
            self,
            "Accès Développeur",
            "Entrez le mot de passe développeur :",
            QLineEdit.EchoMode.Password
        )

        dev_password = self.config.get("security", {}).get("dev_password", "dev123")
        if ok and password == dev_password:
            self.dev_unlocked = True
            self.dev_lock.hide()
            self.dev_content.show()
            QMessageBox.information(self, "Accès autorisé", "Bienvenue dans l'onglet développeur !")
        elif ok:  # Si l'utilisateur a cliqué OK (pas Annuler)
            QMessageBox.warning(self, "Accès refusé", "Mot de passe incorrect")

    def on_dev_test_extract(self):
        """Test l'extraction d'images depuis une URL"""
        url = self.dev_url_test.text().strip()
        if not url:
            self.dev_output.setText("❌ Veuillez entrer une URL")
            return

        self.dev_output.setText(f"🔍 Test d'extraction pour : {url}\n\nEn cours...")

        try:
            # Appeler le service de téléchargement
            image_urls = self.download_service.extract_image_urls(url)

            result = f"✅ Extraction réussie !\n\n"
            result += f"📊 Nombre d'images trouvées : {len(image_urls)}\n\n"
            result += "📝 Liste des URLs :\n"
            for i, img_url in enumerate(image_urls, 1):
                result += f"  {i}. {img_url}\n"

            self.dev_output.setText(result)

        except Exception as e:
            self.dev_output.setText(f"❌ Erreur lors de l'extraction :\n\n{str(e)}")

    # ============ Méthodes existantes (copier depuis l'ancien fichier) ============

    def on_translate_mode_changed(self, _idx: int):
        is_online = self.translate_mode_combo.currentIndex() == 0
        self.api_key_edit.setVisible(is_online)
        self.fallback_chk.setVisible(is_online)

    def on_url_translate_mode_changed(self, _idx: int):
        """Afficher/cacher la clé API selon le mode dans l'onglet URL"""
        is_online = self.url_translate_mode_combo.currentIndex() == 0
        self.url_api_key_edit.setVisible(is_online)
        self.url_fallback_chk.setVisible(is_online)

    def on_url_api_key_changed(self, text: str):
        """Sauvegarder la clé API dans config.json quand elle change"""
        if not hasattr(self, '_api_key_save_pending'):
            self._api_key_save_pending = False

        # Sauvegarder dans la config
        if "api_keys" not in self.config:
            self.config["api_keys"] = {}
        self.config["api_keys"]["deepl_api_key"] = text

        # Sauvegarder dans le fichier
        try:
            config_path = Path(__file__).parent.parent.parent / "config.json"
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)

            # Aussi mettre à jour le champ de l'onglet Local
            if hasattr(self, 'api_key_edit'):
                self.api_key_edit.setText(text)
        except Exception as e:
            print(f"⚠️ Erreur lors de la sauvegarde de la clé API : {e}")

    def on_set_url_export_dir(self):
        """Choisir le dossier d'export pour l'onglet URL"""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choisir le dossier d'export",
            str(Path.home())
        )
        if folder:
            self.url_export_dir = folder
            self.url_export_dir_label.setText(f"📁 Dossier d'export : {folder}")
            # Aussi mettre à jour le dossier d'export de l'onglet Local
            self.export_dir = folder

    def on_choose_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choisir une image",
            str(Path.cwd()),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if not path:
            self.logs.log("ℹ️ Sélection annulée.")
            return

        self.current_image_path = path
        self.progress.setValue(0)
        self.btn_run.setEnabled(True)

        self.image_viewer.clear_boxes()

        try:
            lang_code = self.lang_map.get(self.lang_combo.currentText(), "auto")
            img_preview = self.ocr_service.prepare_preview(path, lang_code)
            self.image_viewer.set_image_array(img_preview, bgr=True)
            self.logs.log("👁️ Aperçu : image pré-traitée OCR affichée")
        except Exception as e:
            self.logs.log(f"⚠️ Aperçu impossible, affichage image originale ({e})")
            self.image_viewer.set_image(path)

        self.logs.log(f"🖼️ Image chargée : {path}")

    # Les autres méthodes (on_run_ocr, on_render_translated, etc.) restent identiques
    # Je les copie depuis l'ancien fichier main_window.py
    # Pour l'instant, je mets un placeholder


    def on_run_ocr(self):
        if not self.current_image_path:
            self.logs.log("❌ Aucune image sélectionnée.")
            return

        ui_lang = self.lang_combo.currentText()
        lang_code = self.lang_map.get(ui_lang, "auto")

        # Traduction settings UI
        mode_str = "online" if self.translate_mode_combo.currentIndex() == 0 else "local"
        translate_mode = cast(TranslatorMode, mode_str)

        api_key = self.api_key_edit.text().strip()
        auto_fallback = self.fallback_chk.isChecked()

        src_lang_ui = ui_lang if ui_lang != "Auto" else "EN"

        self.logs.log("🚀 OCR en cours… (la 1ère fois peut télécharger des modèles)")
        self.logs.log(f"   - Langue : {ui_lang}")
        self.logs.log(f"🌍 Traduction : {'Online (API)' if translate_mode == 'online' else 'Local (offline)'} → {self.tgt_lang_ui}")
        if translate_mode == "online" and not api_key:
            self.logs.log("⚠️ Online choisi mais clé API vide → risque d'échec (fallback local possible).")

        self.progress.setValue(0)
        self.btn_run.setEnabled(False)
        self.btn_choose.setEnabled(False)

        thread = QThread()
        worker = OCRTranslateWorker(
            image_path=self.current_image_path,
            lang_code=lang_code,
            ocr_service=self.ocr_service,
            translate_service=self.translate_service,
            translate_mode=translate_mode,
            api_key=api_key,
            src_lang_ui=src_lang_ui,
            tgt_lang_ui=self.tgt_lang_ui,
            auto_fallback=auto_fallback,
        )
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self.progress.setValue)
        worker.finished.connect(self.on_ocr_translate_finished)
        worker.error.connect(self.on_ocr_error)

        # Clean
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        worker.error.connect(thread.quit)
        worker.error.connect(worker.deleteLater)

        self._thread = thread
        self._worker = worker
        thread.start()

    def on_ocr_translate_finished(self, results: list, translations: list):
        self.btn_run.setEnabled(True)
        self.btn_choose.setEnabled(True)

        self.last_ocr_results = results
        self.last_translations = translations

        if results and translations:
            self.btn_render.setEnabled(True)

        out_img = self.ocr_service.last_output_img
        if out_img is not None:
            try:
                self.image_viewer.set_image_array(out_img, bgr=True)
            except Exception:
                if self.current_image_path:
                    self.logs.log("⚠️ Impossible d'afficher l'image OCR, fallback image originale.")
                    self.image_viewer.set_image(self.current_image_path)
        else:
            if self.current_image_path:
                self.logs.log("⚠️ Image OCR non disponible, affichage image originale.")
                self.image_viewer.set_image(self.current_image_path)

        if not results:
            self.logs.log("⚠️ OCR fini, mais aucun texte détecté.")
            self.image_viewer.clear_boxes()
            return

        boxes = [box for (_, _, box) in results]
        self.image_viewer.set_boxes(boxes)

        self.logs.log(f"✅ OCR terminé : {len(results)} blocs détectés")
        for i, (text, conf, _box) in enumerate(results[:30], start=1):
            self.logs.log(f"  {i:02d}. ({conf:.2f}) {text}")

        if len(results) > 30:
            self.logs.log(f"… +{len(results) - 30} autres blocs (non affichés)")

        if translations:
            self.logs.log("✅ Traduction terminée :")
            orig_texts = [t for (t, _c, _b) in results]
            for i, (orig, tr) in enumerate(list(zip(orig_texts, translations))[:30], start=1):
                self.logs.log(f"  {i:02d}. ORIG: {orig}")
                self.logs.log(f"      FR  : {tr}")
        else:
            self.logs.log("ℹ️ Traduction non effectuée.")

    def on_ocr_error(self, message: str):
        self.btn_run.setEnabled(True)
        self.btn_choose.setEnabled(True)
        self.progress.setValue(0)
        self.logs.log("❌ Erreur OCR/Traduction :")
        self.logs.log(message)
        if self.current_image_path:
            self.image_viewer.clear_boxes()
            self.image_viewer.set_image(self.current_image_path)

    def on_render_translated(self):
        if not self.last_ocr_results or not self.last_translations:
            self.logs.log("❌ Pas de résultats OCR/traduction disponibles")
            return

        if not self.current_image_path:
            self.logs.log("❌ Pas d'image chargée")
            return

        self.logs.log("🎨 Rendu en cours (inpainting + texte traduit)...")
        self.progress.setValue(0)

        try:
            import cv2
            img_bgr = cv2.imread(self.current_image_path)
            if img_bgr is None:
                self.logs.log("❌ Impossible de charger l'image")
                return

            boxes = [box for (_text, _conf, box) in self.last_ocr_results]
            self.progress.setValue(25)

            rendered_img = self.render_service.render_translated_image(
                img_bgr,
                boxes,
                self.last_translations
            )

            self.progress.setValue(75)
            self.last_rendered_img = rendered_img
            self.image_viewer.clear_boxes()
            self.image_viewer.set_image_array(rendered_img, bgr=True)
            self.btn_export.setEnabled(True)
            self.progress.setValue(100)
            self.logs.log("✅ Rendu terminé ! Image avec texte traduit affichée")
            self.logs.log(f"   {len(boxes)} bulles traitées (inpainting + texte)")

        except Exception as e:
            self.logs.log(f"❌ Erreur lors du rendu : {e}")
            import traceback
            self.logs.log(traceback.format_exc())

    def on_export(self):
        if self.last_rendered_img is None:
            self.logs.log("❌ Pas d'image rendue à exporter")
            self.logs.log("   → Clique d'abord sur 'Traduire + Rendre'")
            return

        if not self.last_export_dir:
            self.logs.log("❌ Aucun dossier d'export configuré")
            self.logs.log("   → Clique sur '📁 Dossier d'export...' d'abord")
            return

        output_dir = self.last_export_dir
        self.logs.log(f"📁 Export vers : {output_dir}")
        self.progress.setValue(0)

        try:
            if self.current_image_path:
                base_name = Path(self.current_image_path).stem + "_traduit"
            else:
                base_name = "page_traduite"

            self.progress.setValue(25)
            img_path, pdf_path = self.export_service.export_both(
                self.last_rendered_img,
                output_dir,
                base_name,
                image_format="png"
            )

            self.progress.setValue(100)
            self.logs.log("✅ Export terminé !")
            self.logs.log(f"   📄 Image : {img_path}")
            self.logs.log(f"   📕 PDF   : {pdf_path}")

        except Exception as e:
            self.logs.log(f"❌ Erreur lors de l'export : {e}")
            import traceback
            self.logs.log(traceback.format_exc())
            self.progress.setValue(0)

    def on_run_batch(self):
        if not self.batch_folder_path:
            self.logs.log("❌ Aucun dossier sélectionné pour le batch")
            return

        if not self.last_export_dir:
            self.logs.log("❌ Aucun dossier d'export configuré")
            self.logs.log("   → Clique sur '📁 Dossier d'export...' d'abord")
            return

        self.btn_run_batch.setEnabled(False)
        self.btn_choose_folder.setEnabled(False)
        self.btn_choose.setEnabled(False)
        self.btn_run.setEnabled(False)

        self.logs.log("🚀 Démarrage du traitement batch...")

        lang_code = self.lang_map[self.lang_combo.currentText()]
        chapter_name = Path(self.batch_folder_path).name

        self._batch_worker = BatchWorker(
            folder_path=self.batch_folder_path,
            output_dir=self.last_export_dir,
            chapter_name=chapter_name,
            create_pdf=True,
            lang_code=lang_code,
            ocr_service=self.ocr_service,
            translate_service=self.translate_service,
            render_service=self.render_service,
            batch_service=self.batch_service,
        )

        self._batch_thread = QThread()
        self._batch_worker.moveToThread(self._batch_thread)

        self._batch_thread.started.connect(self._batch_worker.run)
        self._batch_worker.finished.connect(self._on_batch_finished)
        self._batch_worker.error.connect(self._on_batch_error)
        self._batch_worker.progress.connect(self._on_batch_progress)
        self._batch_worker.finished.connect(self._batch_thread.quit)
        self._batch_worker.error.connect(self._batch_thread.quit)

        self._batch_thread.start()

    def _on_batch_progress(self, current: int, total: int, status_text: str):
        if total > 0:
            progress_percent = int((current / total) * 100)
            self.progress.setValue(progress_percent)
        self.logs.log(f"⏳ {status_text}")

    def _on_batch_finished(self, exported_images: List[str], pdf_path: str):
        self.progress.setValue(100)
        self.logs.log("✅ Traitement batch terminé !")
        self.logs.log(f"   📄 {len(exported_images)} images exportées")
        if pdf_path:
            self.logs.log(f"   📕 PDF : {pdf_path}")

        self.btn_run_batch.setEnabled(True)
        self.btn_choose_folder.setEnabled(True)
        self.btn_choose.setEnabled(True)
        self.btn_run.setEnabled(bool(self.current_image_path))

    def _on_batch_error(self, error_msg: str):
        self.logs.log(f"❌ Erreur batch : {error_msg}")
        self.progress.setValue(0)

        self.btn_run_batch.setEnabled(True)
        self.btn_choose_folder.setEnabled(True)
        self.btn_choose.setEnabled(True)
        self.btn_run.setEnabled(bool(self.current_image_path))

    def on_download(self):
        url = self.url_edit.text().strip()
        if not url:
            self.url_logs.log("❌ Aucune URL fournie")
            return

        # Utiliser url_export_dir si configuré, sinon fallback sur last_export_dir
        export_dir = getattr(self, 'url_export_dir', None) or getattr(self, 'last_export_dir', None)
        if not export_dir:
            self.url_logs.log("❌ Aucun dossier d'export configuré")
            self.url_logs.log("   → Cliquez sur '📁 Choisir dossier d'export' d'abord")
            return

        self._start_download(url, auto_process=False)

    def on_download_and_process(self):
        url = self.url_edit.text().strip()
        if not url:
            self.url_logs.log("❌ Aucune URL fournie")
            return

        # Utiliser url_export_dir si configuré, sinon fallback sur last_export_dir
        export_dir = getattr(self, 'url_export_dir', None) or getattr(self, 'last_export_dir', None)
        if not export_dir:
            self.url_logs.log("❌ Aucun dossier d'export configuré")
            self.url_logs.log("   → Cliquez sur '📁 Choisir dossier d'export' d'abord")
            return

        self._start_download(url, auto_process=True)

    def on_stop_download(self):
        """Arrêter le téléchargement en cours"""
        if hasattr(self, '_download_worker') and self._download_worker:
            self.url_logs.log("⏹️ Arrêt du téléchargement en cours...")

            try:
                # Demander au worker de s'arrêter
                self._download_worker.stop()

                # Le worker va lever une InterruptedError qui sera catchée dans run()
                # et émettra un signal error, ce qui va déclencher _on_download_error

            except Exception as e:
                self.url_logs.log(f"⚠️ Erreur lors de l'arrêt : {e}")
        else:
            self.url_logs.log("⚠️ Aucun téléchargement en cours")

    def _start_download(self, url: str, auto_process: bool = False):
        # Utiliser url_export_dir si configuré, sinon fallback sur last_export_dir
        export_dir = getattr(self, 'url_export_dir', None) or getattr(self, 'last_export_dir', None)
        if not export_dir:
            self.url_logs.log("❌ Erreur : dossier d'export non configuré")
            return

        self.btn_download.setEnabled(False)
        self.btn_download_and_process.setEnabled(False)
        self.btn_stop_download.setEnabled(True)
        self.btn_choose.setEnabled(False)
        self.btn_run.setEnabled(False)

        self.url_logs.log(f"⬇️ Téléchargement depuis : {url}")

        self._download_worker = DownloadWorker(
            chapter_url=url,
            base_output_dir=export_dir,
            download_service=self.download_service,
            auto_process=auto_process,
        )

        self._download_thread = QThread()
        self._download_worker.moveToThread(self._download_thread)

        self._download_thread.started.connect(self._download_worker.run)
        self._download_worker.finished.connect(self._on_download_finished)
        self._download_worker.error.connect(self._on_download_error)
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.finished.connect(self._download_thread.quit)
        self._download_worker.error.connect(self._download_thread.quit)

        self._download_thread.start()

    def _on_download_progress(self, current: int, total: int, status_text: str):
        if total > 0:
            progress_percent = int((current / total) * 100)
            self.url_progress.setValue(progress_percent)
        self.url_logs.log(f"⏳ {status_text}")

    def _on_download_finished(self, manga_name: str, chapter_name: str, downloaded_files: List[str]):
        self.url_progress.setValue(100)
        self.url_logs.log(f"✅ Téléchargement terminé !")
        self.url_logs.log(f"   📂 Manga : {manga_name}")
        self.url_logs.log(f"   📄 Chapitre : {chapter_name}")
        self.url_logs.log(f"   🖼️ {len(downloaded_files)} images téléchargées")

        self.btn_download.setEnabled(True)
        self.btn_download_and_process.setEnabled(True)
        self.btn_stop_download.setEnabled(False)
        self.btn_choose.setEnabled(True)

        if self._download_worker and self._download_worker.auto_process:
            self.url_logs.log("🚀 Lancement du traitement batch automatique...")

            export_dir = getattr(self, 'url_export_dir', None) or getattr(self, 'last_export_dir', None)
            if export_dir:
                downloaded_folder = str(Path(export_dir) / manga_name / f"chapitre {chapter_name}")
            else:
                self.url_logs.log("❌ Erreur : dossier d'export non configuré")
                return

            self.batch_folder_path = downloaded_folder
            self.on_run_batch()

    def _on_download_error(self, error_msg: str):
        self.url_logs.log(f"❌ Erreur téléchargement : {error_msg}")
        self.url_progress.setValue(0)

        self.btn_download.setEnabled(True)
        self.btn_download_and_process.setEnabled(True)
        self.btn_stop_download.setEnabled(False)
        self.btn_choose.setEnabled(True)
        self.btn_run.setEnabled(bool(self.current_image_path))

    def on_set_export_dir(self):
        default_dir = ""
        if self.last_export_dir:
            default_dir = self.last_export_dir
        elif self.current_image_path:
            default_dir = str(Path(self.current_image_path).parent)

        output_dir = str(QFileDialog.getExistingDirectory(
            self,
            "Choisir le dossier d'export par défaut",
            default_dir,
            QFileDialog.Option.ShowDirsOnly
        ))

        if not output_dir:
            self.logs.log("❌ Configuration annulée")
            return

        self.last_export_dir = output_dir
        self.logs.log(f"✅ Dossier d'export configuré : {output_dir}")

    def on_choose_folder(self):
        folder_path = QFileDialog.getExistingDirectory(
            self,
            "Choisir un dossier d'images à traiter",
            str(Path.cwd()),
            QFileDialog.Option.ShowDirsOnly
        )

        if not folder_path:
            self.logs.log("ℹ️ Sélection de dossier annulée.")
            return

        self.batch_folder_path = folder_path
        self.btn_run_batch.setEnabled(True)
        self.logs.log(f"📂 Dossier batch sélectionné : {folder_path}")
