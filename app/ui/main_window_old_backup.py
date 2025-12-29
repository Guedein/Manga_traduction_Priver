# app/ui/main_window.py
from __future__ import annotations


from pathlib import Path
from typing import Optional, List, Tuple, cast

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QPushButton, QComboBox, QProgressBar, QLabel,
    QFileDialog, QHBoxLayout, QVBoxLayout, QSplitter, QGroupBox, QLineEdit, QCheckBox,
    QTabWidget, QInputDialog, QMessageBox
)

from app.ui.widgets.image_viewer import ImageViewer
from app.ui.widgets.log_panel import LogPanel
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


class OCRTranslateWorker(QObject):
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
    # 🔒 Normalisation du mode (évite l’erreur Pylance str vs Literal)
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

    def run(self):
        try:
            manga_name, chapter_name, downloaded_files = self.download_service.download_chapter(
                self.chapter_url,
                self.base_output_dir,
                progress_callback=lambda i, t, s: self.progress.emit(i, t, s)
            )
            self.finished.emit(manga_name, chapter_name, downloaded_files)
        except Exception as e:
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    # Mot de passe pour l'onglet développeur (à changer selon vos besoins)
    DEV_PASSWORD = "dev123"

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Manga Translator Pro")
        self.resize(1200, 800)

        self.current_image_path: Optional[str] = None

        # Résultats OCR + traductions (pour render)
        self.last_ocr_results: List[OcrResult] = []
        self.last_translations: List[str] = []
        self.last_rendered_img: Optional[np.ndarray] = None  # Image rendue (pour export)

        # Dossier d'export mémorisé
        self.last_export_dir: Optional[str] = None

        # Dossier batch (pour traitement de dossier complet)
        self.batch_folder_path: Optional[str] = None

        # Flag pour l'accès développeur
        self.dev_unlocked = False

        # OCR service
        self.ocr_service = OCRService()

        # Traduction service
        self.translate_service = TranslateService()

        # Render service
        self.render_service = RenderService()

        # Export service
        self.export_service = ExportService()

        # Batch service
        self.batch_service = BatchService()

        # Download service
        self.download_service = DownloadService()

        # Créer l'interface avec onglets
        self._create_ui()

    def _create_ui(self):
        """Crée l'interface principale avec onglets"""
        # Widget racine
        root = QWidget()
        self.setCentralWidget(root)

        # Créer le système d'onglets
        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self._on_tab_changed) # pyright: ignore[reportAttributeAccessIssue]

        # Créer les 3 onglets
        self._create_local_tab()
        self._create_url_tab() # pyright: ignore[reportAttributeAccessIssue]
        self._create_dev_tab() # pyright: ignore[reportAttributeAccessIssue]

        # Layout principal
        main_layout = QVBoxLayout(root)
        main_layout.addWidget(self.tabs)

    def _create_local_tab(self):
        """Onglet 1: Traitement Local (images du PC)"""
        tab = QWidget()

        # --- Widgets top controls ---
        self.btn_choose = QPushButton("🖼️ Choisir une image")
        self.btn_run = QPushButton("Lancer (OCR)")
        self.btn_run.setEnabled(False)

        self.btn_render = QPushButton("Traduire + Rendre")
        self.btn_render.setEnabled(False)

        self.btn_set_export_dir = QPushButton("📁 Dossier d'export...")
        self.btn_export = QPushButton("Exporter")
        self.btn_export.setEnabled(False)

        # Boutons batch
        self.btn_choose_folder = QPushButton("📂 Choisir un dossier")
        self.btn_run_batch = QPushButton("▶ Traiter dossier (batch)")
        self.btn_run_batch.setEnabled(False)

        # Téléchargement depuis URL
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("URL du chapitre (ex: https://manhuaus.com/manga/...)")
        self.btn_download = QPushButton("⬇ Télécharger")
        self.btn_download_and_process = QPushButton("⬇ Télécharger + Traiter")

        self.lang_combo = QComboBox()
        self.lang_map = {
            "Auto": "auto",
            "EN": "en",
            "CH": "ch",
            "JP": "jp",
            "KR": "kr",
        }
        self.lang_combo.addItems(list(self.lang_map.keys()))

        # --- Traduction (choix A/B) ---
        self.translate_mode_combo = QComboBox()
        self.translate_mode_combo.addItems(["Online (API)", "Local (offline)"])

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setPlaceholderText("Cle API (DeepL) - visible seulement en Online")
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)

        self.fallback_chk = QCheckBox("Fallback auto Online → Local")
        self.fallback_chk.setChecked(True)

        self.tgt_lang_ui = "FR"

        self.mode_label = QLabel("Mode : Pro")
        self.mode_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        # --- Preview + Logs ---
        self.image_viewer = ImageViewer()
        self.logs = LogPanel()

        # --- Layout build ---
        root = QWidget()
        self.setCentralWidget(root)

        top_left = QHBoxLayout()
        top_left.addWidget(self.btn_choose)
        top_left.addWidget(self.btn_run)
        top_left.addWidget(self.btn_render)
        top_left.addSpacing(20)
        top_left.addWidget(self.btn_set_export_dir)
        top_left.addWidget(self.btn_export)
        top_left.addSpacing(20)
        top_left.addWidget(self.btn_choose_folder)
        top_left.addWidget(self.btn_run_batch)

        top_right = QHBoxLayout()
        top_right.addWidget(QLabel("Langue source :"))
        top_right.addWidget(self.lang_combo)

        top_right.addSpacing(18)
        top_right.addWidget(QLabel("Traduction :"))
        top_right.addWidget(self.translate_mode_combo)
        top_right.addWidget(self.api_key_edit)
        top_right.addWidget(self.fallback_chk)

        top_right.addStretch(1)
        top_right.addWidget(self.mode_label)

        top_bar = QHBoxLayout()
        top_bar.addLayout(top_left)
        top_bar.addStretch(1)
        top_bar.addLayout(top_right)

        # URL download bar
        url_bar = QHBoxLayout()
        url_bar.addWidget(QLabel("URL:"))
        url_bar.addWidget(self.url_edit)
        url_bar.addWidget(self.btn_download)
        url_bar.addWidget(self.btn_download_and_process)

        preview_group = QGroupBox("Preview image")
        preview_layout = QVBoxLayout(preview_group)
        preview_layout.setContentsMargins(0, 0, 0, 0)   # ✅
        preview_layout.setSpacing(0)                    # ✅
        preview_layout.addWidget(self.image_viewer)

        logs_group = QGroupBox("Logs")
        logs_layout = QVBoxLayout(logs_group)
        logs_layout.setContentsMargins(0, 0, 0, 0)      # ✅
        logs_layout.setSpacing(0)                       # ✅
        logs_layout.addWidget(self.logs)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(preview_group)
        splitter.addWidget(logs_group)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setChildrenCollapsible(False)  # ✅ évite qu’un panneau s’écrase bizarrement
        splitter.setHandleWidth(8)              # optionnel, plus facile à choper
        splitter.setSizes([800, 300])           # ✅ preview plus large que logs


        main_layout = QVBoxLayout(root)
        main_layout.addLayout(top_bar)
        main_layout.addLayout(url_bar)
        main_layout.addWidget(self.progress)
        main_layout.addWidget(splitter, 1)  # ✅ le "1" donne tout le stretch au splitter


        # --- Signals ---
        self.btn_choose.clicked.connect(self.on_choose_image)
        self.btn_run.clicked.connect(self.on_run_ocr)
        self.btn_render.clicked.connect(self.on_render_translated)
        self.btn_set_export_dir.clicked.connect(self.on_set_export_dir)
        self.btn_export.clicked.connect(self.on_export)
        self.btn_choose_folder.clicked.connect(self.on_choose_folder)
        self.btn_run_batch.clicked.connect(self.on_run_batch)
        self.btn_download.clicked.connect(self.on_download)
        self.btn_download_and_process.clicked.connect(self.on_download_and_process)
        self.translate_mode_combo.currentIndexChanged.connect(self.on_translate_mode_changed)

        # --- Thread refs ---
        self._thread: Optional[QThread] = None
        self._worker: Optional[OCRTranslateWorker] = None
        self._batch_thread: Optional[QThread] = None
        self._batch_worker: Optional[BatchWorker] = None
        self._download_thread: Optional[QThread] = None
        self._download_worker: Optional[DownloadWorker] = None

        # --- Initial logs ---
        self.logs.log("✅ App démarrée.")
        self.logs.log("➡️ Choisis une image, puis clique sur 'Lancer (OCR)'.")
        self.logs.log("ℹ️ Astuce: les rectangles sont alignés sur l'image pré-traitée (OCR), pas toujours sur l'originale.")
        self.logs.log("🗣️ Traduction: tu peux choisir Online (API) ou Local (offline).")

        self.on_translate_mode_changed(self.translate_mode_combo.currentIndex())

    # ---------------- UI Actions ----------------
    def on_translate_mode_changed(self, _idx: int):
        is_online = self.translate_mode_combo.currentIndex() == 0
        self.api_key_edit.setVisible(is_online)
        self.fallback_chk.setVisible(is_online)

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

        # ✅ COHÉRENCE DU REPÈRE : toujours afficher l'image pré-traitée OCR
        # Ainsi, quand les boxes arriveront, elles seront dans le bon repère
        try:
            lang_code = self.lang_map.get(self.lang_combo.currentText(), "auto")
            img_preview = self.ocr_service.prepare_preview(path, lang_code)
            self.image_viewer.set_image_array(img_preview, bgr=True)
            self.logs.log("👁️ Preview : image pré-traitée OCR affichée (repère cohérent).")
        except Exception as e:
            # Fallback sécurité : image originale
            self.logs.log(f"⚠️ Preview OCR impossible, image originale affichée ({e})")
            self.logs.log("⚠️ ATTENTION : les boxes risquent d'être désalignées si OCR resize l'image.")
            self.image_viewer.set_image(path)

        self.logs.log(f"🖼️ Image chargée : {path}")

    def on_run_ocr(self):
        if not self.current_image_path:
            self.logs.log("❌ Aucune image sélectionnée.")
            return

        ui_lang = self.lang_combo.currentText()
        lang_code = self.lang_map.get(ui_lang, "auto")

        # Traduction settings UI
        mode_str = "online" if self.translate_mode_combo.currentIndex() == 0 else "local"
        translate_mode = cast(TranslatorMode, mode_str)  # ✅ FIX Pylance (str -> Literal)

        api_key = self.api_key_edit.text().strip()
        auto_fallback = self.fallback_chk.isChecked()

        src_lang_ui = ui_lang if ui_lang != "Auto" else "EN"

        self.logs.log("🚀 OCR en cours… (la 1ère fois peut télécharger des modèles)")
        self.logs.log(f"   - Langue : {ui_lang}")
        self.logs.log(f"🌍 Traduction : {'Online (API)' if translate_mode == 'online' else 'Local (offline)'} → {self.tgt_lang_ui}")
        if translate_mode == "online" and not api_key:
            self.logs.log("⚠️ Online choisi mais clé API vide → risque d’échec (fallback local possible).")

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

    # ---------------- OCR Callbacks ----------------
    def on_ocr_translate_finished(self, results: list, translations: list):
        self.btn_run.setEnabled(True)
        self.btn_choose.setEnabled(True)

        # Sauvegarder résultats pour render
        self.last_ocr_results = results
        self.last_translations = translations

        # Activer bouton render si on a des résultats
        if results and translations:
            self.btn_render.setEnabled(True)

        # ✅ COHÉRENCE DU REPÈRE : réafficher l'image OCR pré-traitée
        # (celle qui a été envoyée au modèle, et dans laquelle les boxes sont définies)
        out_img = self.ocr_service.last_output_img
        if out_img is not None:
            try:
                self.image_viewer.set_image_array(out_img, bgr=True)
            except Exception:
                # Fallback : image originale (risque de désalignement)
                if self.current_image_path:
                    self.logs.log("⚠️ Impossible d'afficher l'image OCR, fallback image originale.")
                    self.logs.log("⚠️ Les boxes risquent d'être désalignées.")
                    self.image_viewer.set_image(self.current_image_path)
        else:
            # Pas d'image OCR disponible : afficher l'originale
            if self.current_image_path:
                self.logs.log("⚠️ Image OCR non disponible, affichage image originale.")
                self.logs.log("⚠️ Les boxes risquent d'être désalignées.")
                self.image_viewer.set_image(self.current_image_path)

        if not results:
            self.logs.log("⚠️ OCR fini, mais aucun texte détecté.")
            self.image_viewer.clear_boxes()
            return

        # ✅ Extraire les boxes (elles sont dans le repère de out_img)
        boxes = [box for (_, _, box) in results]
        self.image_viewer.set_boxes(boxes)

        self.logs.log(f"✅ OCR terminé : {len(results)} blocs détectés")
        for i, (text, conf, _box) in enumerate(results[:30], start=1):
            self.logs.log(f"  {i:02d}. ({conf:.2f}) {text}")

        if len(results) > 30:
            self.logs.log(f"… +{len(results) - 30} autres blocs (non affichés)")

        # ✅ FIX zip slicing (zip non indexable)
        if translations:
            self.logs.log("✅ Traduction terminée :")
            orig_texts = [t for (t, _c, _b) in results]
            for i, (orig, tr) in enumerate(list(zip(orig_texts, translations))[:30], start=1):
                self.logs.log(f"  {i:02d}. ORIG: {orig}")
                self.logs.log(f"      FR  : {tr}")
        else:
            self.logs.log("ℹ️ Traduction non effectuée (pas de texte / erreur / clé manquante / local non prêt).")

    def on_ocr_error(self, message: str):
        self.btn_run.setEnabled(True)
        self.btn_choose.setEnabled(True)
        self.progress.setValue(0)

        self.logs.log("❌ Erreur OCR/Traduction :")
        self.logs.log(message)

        if self.current_image_path:
            self.image_viewer.clear_boxes()
            self.image_viewer.set_image(self.current_image_path)

    # ---------------- Render Callback ----------------
    def on_render_translated(self):
        """Applique inpainting + rendu texte traduit"""
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

            # Charger image originale
            img_bgr = cv2.imread(self.current_image_path)
            if img_bgr is None:
                self.logs.log("❌ Impossible de charger l'image")
                return

            # Extraire boxes
            boxes = [box for (_text, _conf, box) in self.last_ocr_results]

            self.progress.setValue(25)

            # Appliquer rendu (inpainting + texte)
            rendered_img = self.render_service.render_translated_image(
                img_bgr,
                boxes,
                self.last_translations
            )

            self.progress.setValue(75)

            # Sauvegarder image rendue pour export
            self.last_rendered_img = rendered_img

            # Afficher résultat
            self.image_viewer.clear_boxes()  # Pas de boxes sur image rendue
            self.image_viewer.set_image_array(rendered_img, bgr=True)

            # Activer bouton export
            self.btn_export.setEnabled(True)

            self.progress.setValue(100)
            self.logs.log("✅ Rendu terminé ! Image avec texte traduit affichée")
            self.logs.log(f"   {len(boxes)} bulles traitées (inpainting + texte)")

        except Exception as e:
            self.logs.log(f"❌ Erreur lors du rendu : {e}")
            import traceback
            self.logs.log(traceback.format_exc())

    # ---------------- Export Callbacks ----------------
    def on_set_export_dir(self):
        """Configure le dossier d'export (une seule fois)"""
        # Dossier par défaut : dernier utilisé ou dossier de l'image source
        default_dir = ""
        if self.last_export_dir:
            default_dir = self.last_export_dir
        elif self.current_image_path:
            default_dir = str(Path(self.current_image_path).parent)

        # Dialogue pour choisir le dossier
        output_dir = str(QFileDialog.getExistingDirectory(
            self,
            "Choisir le dossier d'export par défaut",
            default_dir,
            QFileDialog.Option.ShowDirsOnly
        ))

        if not output_dir:
            self.logs.log("❌ Configuration annulée")
            return

        # Mémoriser le dossier
        self.last_export_dir = output_dir
        self.logs.log(f"✅ Dossier d'export configuré : {output_dir}")

    def on_export(self):
        """Exporte l'image traduite directement (PNG + PDF)"""
        if self.last_rendered_img is None:
            self.logs.log("❌ Pas d'image rendue à exporter")
            self.logs.log("   → Clique d'abord sur 'Traduire + Rendre'")
            return

        # Vérifier si un dossier d'export est configuré
        if not self.last_export_dir:
            self.logs.log("❌ Aucun dossier d'export configuré")
            self.logs.log("   → Clique sur '📁 Dossier d'export...' d'abord")
            return

        output_dir = self.last_export_dir

        self.logs.log(f"📁 Export vers : {output_dir}")
        self.progress.setValue(0)

        try:
            # Nom de base (basé sur l'image source)
            if self.current_image_path:
                import os
                base_name = Path(self.current_image_path).stem + "_traduit"
            else:
                base_name = "page_traduite"

            self.progress.setValue(25)

            # Export image + PDF
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

    # ---------------- Batch Processing ----------------
    def on_choose_folder(self):
        """Choisir un dossier pour traitement batch"""
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

    def on_run_batch(self):
        """Lance le traitement batch d'un dossier complet"""
        if not self.batch_folder_path:
            self.logs.log("❌ Aucun dossier sélectionné pour le batch")
            return

        if not self.last_export_dir:
            self.logs.log("❌ Aucun dossier d'export configuré")
            self.logs.log("   → Clique sur '📁 Dossier d'export...' d'abord")
            return

        # Désactiver les boutons pendant le traitement
        self.btn_run_batch.setEnabled(False)
        self.btn_choose_folder.setEnabled(False)
        self.btn_choose.setEnabled(False)
        self.btn_run.setEnabled(False)

        self.logs.log("🚀 Démarrage du traitement batch...")

        # Récupérer les paramètres
        lang_code = self.lang_map[self.lang_combo.currentText()]
        chapter_name = Path(self.batch_folder_path).name  # Nom du dossier comme nom de chapitre

        # Créer le worker batch
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

        # Créer le thread
        self._batch_thread = QThread()
        self._batch_worker.moveToThread(self._batch_thread)

        # Connecter les signaux
        self._batch_thread.started.connect(self._batch_worker.run)
        self._batch_worker.finished.connect(self._on_batch_finished)
        self._batch_worker.error.connect(self._on_batch_error)
        self._batch_worker.progress.connect(self._on_batch_progress)
        self._batch_worker.finished.connect(self._batch_thread.quit)
        self._batch_worker.error.connect(self._batch_thread.quit)

        # Démarrer
        self._batch_thread.start()

    def _on_batch_progress(self, current: int, total: int, status_text: str):
        """Callback de progression batch"""
        if total > 0:
            progress_percent = int((current / total) * 100)
            self.progress.setValue(progress_percent)
        self.logs.log(f"⏳ {status_text}")

    def _on_batch_finished(self, exported_images: List[str], pdf_path: str):
        """Callback de fin de batch"""
        self.progress.setValue(100)
        self.logs.log("✅ Traitement batch terminé !")
        self.logs.log(f"   📄 {len(exported_images)} images exportées")
        if pdf_path:
            self.logs.log(f"   📕 PDF : {pdf_path}")

        # Réactiver les boutons
        self.btn_run_batch.setEnabled(True)
        self.btn_choose_folder.setEnabled(True)
        self.btn_choose.setEnabled(True)
        self.btn_run.setEnabled(bool(self.current_image_path))

    def _on_batch_error(self, error_msg: str):
        """Callback d'erreur batch"""
        self.logs.log(f"❌ Erreur batch : {error_msg}")
        self.progress.setValue(0)

        # Réactiver les boutons
        self.btn_run_batch.setEnabled(True)
        self.btn_choose_folder.setEnabled(True)
        self.btn_choose.setEnabled(True)
        self.btn_run.setEnabled(bool(self.current_image_path))

    # ---------------- Download from URL ----------------
    def on_download(self):
        """Télécharge un chapitre depuis une URL (sans traitement)"""
        url = self.url_edit.text().strip()
        if not url:
            self.logs.log("❌ Aucune URL fournie")
            return

        if not self.last_export_dir:
            self.logs.log("❌ Aucun dossier d'export configuré")
            self.logs.log("   → Clique sur '📁 Dossier d'export...' d'abord")
            return

        self._start_download(url, auto_process=False)

    def on_download_and_process(self):
        """Télécharge un chapitre depuis une URL puis lance le traitement batch"""
        url = self.url_edit.text().strip()
        if not url:
            self.logs.log("❌ Aucune URL fournie")
            return

        if not self.last_export_dir:
            self.logs.log("❌ Aucun dossier d'export configuré")
            self.logs.log("   → Clique sur '📁 Dossier d'export...' d'abord")
            return

        self._start_download(url, auto_process=True)

    def _start_download(self, url: str, auto_process: bool = False):
        """Démarre le téléchargement"""
        # Vérifier que le dossier d'export est configuré
        if not self.last_export_dir:
            self.logs.log("❌ Erreur : dossier d'export non configuré")
            return

        # Désactiver les boutons
        self.btn_download.setEnabled(False)
        self.btn_download_and_process.setEnabled(False)
        self.btn_choose.setEnabled(False)
        self.btn_run.setEnabled(False)

        self.logs.log(f"⬇️ Téléchargement depuis : {url}")

        # Créer le worker
        self._download_worker = DownloadWorker(
            chapter_url=url,
            base_output_dir=self.last_export_dir,
            download_service=self.download_service,
            auto_process=auto_process,
        )

        # Créer le thread
        self._download_thread = QThread()
        self._download_worker.moveToThread(self._download_thread)

        # Connecter les signaux
        self._download_thread.started.connect(self._download_worker.run)
        self._download_worker.finished.connect(self._on_download_finished)
        self._download_worker.error.connect(self._on_download_error)
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.finished.connect(self._download_thread.quit)
        self._download_worker.error.connect(self._download_thread.quit)

        # Démarrer
        self._download_thread.start()

    def _on_download_progress(self, current: int, total: int, status_text: str):
        """Callback de progression téléchargement"""
        if total > 0:
            progress_percent = int((current / total) * 100)
            self.progress.setValue(progress_percent)
        self.logs.log(f"⏳ {status_text}")

    def _on_download_finished(self, manga_name: str, chapter_name: str, downloaded_files: List[str]):
        """Callback de fin de téléchargement"""
        self.progress.setValue(100)
        self.logs.log(f"✅ Téléchargement terminé !")
        self.logs.log(f"   📂 Manga : {manga_name}")
        self.logs.log(f"   📄 Chapitre : {chapter_name}")
        self.logs.log(f"   🖼️ {len(downloaded_files)} images téléchargées")

        # Réactiver les boutons
        self.btn_download.setEnabled(True)
        self.btn_download_and_process.setEnabled(True)
        self.btn_choose.setEnabled(True)

        # Si auto-process, lancer le traitement batch
        if self._download_worker and self._download_worker.auto_process:
            self.logs.log("🚀 Lancement du traitement batch automatique...")

            # Le dossier téléchargé est : last_export_dir/manga_name/chapitre chapter_name/
            if self.last_export_dir:
                downloaded_folder = str(Path(self.last_export_dir) / manga_name / f"chapitre {chapter_name}")
            else:
                self.logs.log("❌ Erreur : dossier d'export non configuré")
                return

            # Mettre à jour le batch_folder_path
            self.batch_folder_path = downloaded_folder

            # Lancer le batch
            self.on_run_batch()

    def _on_download_error(self, error_msg: str):
        """Callback d'erreur téléchargement"""
        self.logs.log(f"❌ Erreur téléchargement : {error_msg}")
        self.progress.setValue(0)

        # Réactiver les boutons
        self.btn_download.setEnabled(True)
        self.btn_download_and_process.setEnabled(True)
        self.btn_choose.setEnabled(True)
        self.btn_run.setEnabled(bool(self.current_image_path))
