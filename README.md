# Manga Translator

Application Windows de traduction de mangas utilisant OCR (PaddleX) et traduction automatique.

## 🚀 Quick Start

```bash
# Activer l'environnement virtuel
.venv\Scripts\activate

# Installer les dépendances
pip install -r requirements.txt

# Lancer l'application
python -m app.main
# ou
lancer_app.bat
```

## 📚 Documentation

### Problème d'alignement Image/Boxes (RÉSOLU ✅)

Une solution complète a été implémentée pour corriger le désalignement entre l'image affichée et les rectangles OCR.

**Documentation complète** : [INDEX_DOCUMENTATION.md](INDEX_DOCUMENTATION.md)

**Quick start documentation** :
- **Référence rapide** : [QUICK_REFERENCE.md](QUICK_REFERENCE.md) - Règle d'or + checklist
- **Explication visuelle** : [VISUAL_EXPLICATION.md](VISUAL_EXPLICATION.md) - Schémas avant/après
- **Résumé solution** : [RESUME_SOLUTION.md](RESUME_SOLUTION.md) - Vue d'ensemble
- **Tests validation** : [TESTS_VALIDATION.md](TESTS_VALIDATION.md) - Tests manuels

### Règle d'or

> **L'image affichée et les boxes OCR partagent TOUJOURS le même repère source.**

Cela signifie que l'image pré-traitée OCR (celle envoyée au modèle) est affichée dès le chargement, garantissant un alignement parfait des rectangles.

## 🏗️ Architecture

```
manga_translator/
├── app/
│   ├── core/           # Logique métier
│   ├── services/       # OCR, traduction
│   │   ├── ocr_service.py      # ⚠️ prepare_preview() DOIT être cohérent avec run()
│   │   └── translate_service.py
│   ├── ui/             # Interface PySide6
│   │   ├── main_window.py      # ⚠️ Affiche toujours image pré-traitée
│   │   └── widgets/
│   │       └── image_viewer.py # ⚠️ Mode FIT uniquement (min scale)
│   └── utils/          # Utilitaires
├── output/             # Images traduites
├── temp/               # Fichiers temporaires
└── *.md                # Documentation (voir INDEX_DOCUMENTATION.md)
```

## 🔧 Configuration

**[config.json](config.json)** : Configuration de l'application (clés API, etc.)

## ⚠️ Notes importantes

### Pour développeurs

Avant de modifier le code d'affichage :
1. Lis [QUICK_REFERENCE.md](QUICK_REFERENCE.md) → Checklist
2. Vérifie [COHERENCE_REPERE.md](COHERENCE_REPERE.md) → Règles strictes
3. Teste avec [TESTS_VALIDATION.md](TESTS_VALIDATION.md)

### Scénarios à éviter

❌ **NE JAMAIS** afficher l'image originale si les boxes sont dans le repère OCR pré-traité
❌ **NE JAMAIS** utiliser mode FILL (`max` scale) → utilise FIT (`min` scale)
❌ **NE JAMAIS** ajouter preprocessing uniquement dans `run()` → ajoute aussi dans `prepare_preview()`

✅ **TOUJOURS** afficher l'image pré-traitée OCR
✅ **TOUJOURS** utiliser la même transformation pour l'image ET les boxes
✅ **TOUJOURS** respecter la règle d'or

## 📝 License

(À compléter)
