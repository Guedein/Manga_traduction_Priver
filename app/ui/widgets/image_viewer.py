# app/ui/widgets/image_viewer.py
from __future__ import annotations

from typing import List, Tuple, Optional, Any

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore


Point = Tuple[int, int]
PolyBox = List[Point]            # [(x,y), (x,y), (x,y), (x,y)]
AnyBox = Any


class ImageViewer(QWidget):
    """
    Widget d'affichage d'image + overlay (rectangles / polys OCR)

    RÈGLE D'OR:
    - L'image affichée et les boxes partagent TOUJOURS le même repère source
    - Mode FIT uniquement : l'image entière est visible, centrée, sans crop/zoom
    - La transformation image→widget est calculée une seule fois et appliquée
      identiquement à l'image ET aux boxes

    Repère source : coordonnées dans l'image affichée (ex: 0,0 → coin haut-gauche image)
    Repère widget : coordonnées dans le QWidget (ex: 0,0 → coin haut-gauche widget)

    Transformation : (sx, sy, ox, oy)
      - sx, sy : facteurs d'échelle (target_width / img_width, target_height / img_height)
      - ox, oy : offsets de centrage dans le widget
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setAutoFillBackground(True)

        self._pixmap: Optional[QPixmap] = None
        self._img_w: int = 0
        self._img_h: int = 0
        self._boxes: List[PolyBox] = []

        # Rect où l'image est dessinée dans le widget (fit + centré)
        self._target_rect: QRectF = QRectF()

        # Cache transformation (invalidated on resize)
        self._cached_transform: Optional[Tuple[float, float, float, float]] = None

    # ---------------- Public API ----------------

    def clear_boxes(self) -> None:
        self._boxes = []
        self.update()

    def set_boxes(self, boxes: List[AnyBox]) -> None:
        out: List[PolyBox] = []
        for b in (boxes or []):
            nb = self._normalize_box(b)
            if nb:
                out.append(nb)
        self._boxes = out
        self.update()

    def set_image(self, path: str) -> None:
        img = QImage(path)
        if img.isNull():
            self._pixmap = None
            self._img_w = 0
            self._img_h = 0
            self._boxes = []
            self._target_rect = QRectF()
            self.update()
            return

        pm = QPixmap.fromImage(img)

        # ⚠️ important HiDPI: taille logique (pas device pixels)
        dpr = pm.devicePixelRatio() or 1.0
        self._pixmap = pm
        self._img_w = int(pm.width() / dpr)
        self._img_h = int(pm.height() / dpr)

        self._recalc_target_rect()
        self.update()

    def set_image_array(self, arr, bgr: bool = True) -> None:
        """
        arr: numpy array OpenCV (H,W,3) en BGR (souvent) ou RGB
        bgr=True -> conversion BGR->RGB
        """
        if np is None:
            raise RuntimeError("numpy n'est pas dispo, impossible d'utiliser set_image_array")

        if arr is None:
            self._pixmap = None
            self._img_w = 0
            self._img_h = 0
            self._target_rect = QRectF()
            self.update()
            return

        if not hasattr(arr, "shape"):
            raise TypeError("set_image_array attend un numpy array (shape HxWxC)")

        a = arr
        if a.ndim == 2:
            a = np.stack([a, a, a], axis=-1)
        if a.ndim != 3 or a.shape[2] < 3:
            raise ValueError(f"Array invalide: shape={getattr(a,'shape',None)}")

        a = a[:, :, :3]
        if bgr:
            a = a[:, :, ::-1]  # BGR -> RGB

        h, w, _ = a.shape
        a = np.ascontiguousarray(a)
        bytes_per_line = 3 * w
        qimg = QImage(a.data, w, h, bytes_per_line, QImage.Format.Format_RGB888).copy()

        pm = QPixmap.fromImage(qimg)

        dpr = pm.devicePixelRatio() or 1.0
        self._pixmap = pm
        self._img_w = int(pm.width() / dpr)
        self._img_h = int(pm.height() / dpr)

        self._recalc_target_rect()
        self.update()

    # ---------------- Qt Events ----------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._recalc_target_rect()
        self._cached_transform = None  # Invalidate cache

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        painter.fillRect(self.rect(), self.palette().window())

        if self._pixmap is None or self._pixmap.isNull() or self._target_rect.isNull():
            return

        # ✅ Dessin image: on dessine TOUTE l'image dans target_rect (mode FIT)
        # src_rect = rectangle source dans l'image (coordonnées image)
        # target_rect = rectangle destination dans le widget (coordonnées widget)
        src_rect = QRectF(0, 0, float(self._img_w), float(self._img_h))
        painter.drawPixmap(self._target_rect, self._pixmap, src_rect)

        if not self._boxes:
            return

        pen = QPen(Qt.GlobalColor.red)
        pen.setWidth(3)
        painter.setPen(pen)

        # ✅ Transform: coordonnées image → coordonnées widget (cached)
        # IMPORTANTE : cette transformation est IDENTIQUE à celle appliquée à l'image
        # donc les boxes restent alignées
        if self._cached_transform is None:
            self._cached_transform = self._compute_transform()
        sx, sy, ox, oy = self._cached_transform

        for poly in self._boxes:
            # Appliquer la même transformation que l'image : scale puis offset
            pts = [QPointF(ox + sx * float(x), oy + sy * float(y)) for (x, y) in poly]
            if len(pts) >= 2:
                for i in range(len(pts)):
                    painter.drawLine(pts[i], pts[(i + 1) % len(pts)])

    # ---------------- Internal ----------------

    def _recalc_target_rect(self) -> None:
        """
        Calcule le rectangle où l'image est dessinée (centré, keep aspect ratio / FIT).

        Principe :
        1. Calculer le scale pour FIT l'image dans le widget (le plus petit ratio)
        2. Calculer la taille finale de l'image dans le widget (tw, th)
        3. Centrer l'image dans le widget (x, y)

        Ce rectangle sert de référence pour drawPixmap ET pour la transformation des boxes.
        """
        if self._pixmap is None or self._pixmap.isNull() or self._img_w <= 0 or self._img_h <= 0:
            self._target_rect = QRectF()
            return

        w = float(self.width())
        h = float(self.height())
        iw = float(self._img_w)
        ih = float(self._img_h)

        if w <= 1 or h <= 1:
            self._target_rect = QRectF()
            return

        # ✅ FIT : prendre le scale minimal pour que l'image entière soit visible
        scale = min(w / iw, h / ih)

        # Dimensions finales de l'image dans le widget
        tw = iw * scale
        th = ih * scale

        # Centrage
        x = (w - tw) / 2.0
        y = (h - th) / 2.0

        self._target_rect = QRectF(x, y, tw, th)
        self._cached_transform = None  # Invalidate transform cache

    def _compute_transform(self) -> Tuple[float, float, float, float]:
        """
        Retourne (sx, sy, ox, oy) pour mapper les coordonnées image → widget

        - sx, sy : facteurs d'échelle (identiques car on garde aspect ratio)
        - ox, oy : offsets de centrage dans le widget

        Formule : widget_coord = offset + scale * image_coord
          widget_x = ox + sx * image_x
          widget_y = oy + sy * image_y

        ✅ COHÉRENCE : cette transformation est EXACTEMENT celle appliquée par drawPixmap
        donc les boxes s'alignent parfaitement sur l'image affichée.
        """
        if self._img_w <= 0 or self._img_h <= 0 or self._target_rect.isNull():
            return 1.0, 1.0, 0.0, 0.0

        # Scale : target_rect contient l'image FIT
        sx = float(self._target_rect.width()) / float(self._img_w)
        sy = float(self._target_rect.height()) / float(self._img_h)

        # Offset : position du coin haut-gauche de l'image dans le widget
        ox = float(self._target_rect.left())
        oy = float(self._target_rect.top())

        return sx, sy, ox, oy

    def _normalize_box(self, box: AnyBox) -> PolyBox:
        """Convertit plusieurs formats de box en poly [(x,y)*4]"""
        if box is None:
            return []

        # numpy array -> list
        if np is not None and isinstance(box, np.ndarray):
            box = box.tolist()

        # dict bbox
        if isinstance(box, dict):
            if all(k in box for k in ("x1", "y1", "x2", "y2")):
                x1, y1, x2, y2 = int(box["x1"]), int(box["y1"]), int(box["x2"]), int(box["y2"])
                return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            return []

        # bbox [x1,y1,x2,y2]
        if isinstance(box, (list, tuple)) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
            x1, y1, x2, y2 = (int(box[0]), int(box[1]), int(box[2]), int(box[3]))
            return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]

        # poly [(x,y)...]
        if isinstance(box, (list, tuple)) and len(box) >= 4:
            if isinstance(box[0], (list, tuple)) and len(box[0]) >= 2:
                pts: PolyBox = []
                for p in list(box)[:4]:
                    if isinstance(p, (list, tuple)) and len(p) >= 2:
                        pts.append((int(p[0]), int(p[1])))
                return pts

        return []
