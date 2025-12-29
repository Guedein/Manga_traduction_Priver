# 📘 Instructions de projet — Manga / Manhua Translator

## 🎯 Objectif du projet
Créer un **logiciel Windows** permettant de **traduire automatiquement** des chapitres de **manga / manhua / manhwa** à partir d’images, avec :

- **OCR local**
- **Traduction par IA**
- **Réécriture propre** du texte dans les bulles
- **Export final** en images + **1 PDF par chapitre**

Le logiciel doit être **utilisable par un utilisateur non technique** (UI simple, **en français**).

---

## 🧱 Architecture générale imposée
Le projet suit une architecture **modulaire et évolutive**, séparant clairement :

- le **moteur** (OCR, traduction, rendu)
- l’**interface Windows**
- les **services externes** (IA, PDF, cache)

---

## 🛠️ Technologies imposées

### Langage
- **Python**

### UI Windows
- **PySide6 (Qt)**

### OCR & détection de texte
- **EasyOCR** (GPU si dispo, **CPU fallback automatique**)
- **Détection de texte** : EasyOCR (multi-langues)

### Traduction IA
- **API OpenAI**

### Rendu image 
- **OpenCV** (inpainting)
- **Pillow** (texte, polices)

### PDF
- **ReportLab**

### Packaging Windows
- **PyInstaller**

---

## 🌍 Langues

### Langues source
- **Auto / EN / CH / JP / KR**

### Langue cible
- **FR (fixe)**

### Cas majoritaire
- **EN → FR**

### Règles de traduction
La traduction doit être :
- **naturelle**
- **courte**
- **adaptée** à des dialogues de manga
- **sans ajout d’explications**

---

## 🖼️ Entrées & sorties

### Entrées
- **Dossier d’images** (pages d’un chapitre)
- *(Plus tard)* **URL** d’un chapitre

### Sorties
Pour chaque chapitre :
- **Images traduites** (`001_trad.png`, etc.)
- **Un seul PDF** par chapitre
- Un fichier projet **`.json`** contenant :
  - bulles
  - bbox
  - texte OCR
  - texte traduit  
  *(pour réédition sans refaire OCR/traduction)*

---

## ✏️ Rendu du texte

### Mode par défaut : Mode B — Pro
- **Inpainting** du texte original (fond reconstruit)
- **Réécriture** du texte traduit
- **Auto-size**, **auto-wrap**, **centrage**
- **Marges internes configurables**

---

## 🔤 Gestion des polices
- Police **neutre et lisible** par défaut
- Police **configurable**
- Possibilité pour l’utilisateur de :
  - choisir une police interne
  - charger une police `.ttf` personnalisée
- Le changement de police doit permettre un **re-rendu complet** du chapitre

⚠️ **Aucune police ne doit être codée en dur.**

---

## 🔐 Clé API OpenAI
- Entrée via l’interface graphique
- Stockage local **sécurisé** (AppData utilisateur)
- Test de validité depuis l’UI
- **Aucune clé ne doit apparaître dans le code**

---

## 🧠 Cache & performance
- Cache local des traductions
- Pas de double appel IA pour le même texte
- Pipeline robuste : **une erreur ne doit jamais faire planter l’app**

---

## 📦 Packaging
- Génération d’un **`.exe` Windows**
- Fonctionnement **sans Python installé**
- Mode **CPU obligatoire** si GPU indisponible

---

## 🪜 Méthodologie de développement imposée
Le développement se fait par **étapes strictes**, dans l’ordre :

1. UI minimale (choix image, logs, preview)
2. OCR + détection bulles sur 1 image
3. Traduction IA + cache
4. Inpainting + rendu texte
5. Export image + PDF (1 page)
6. Traitement d’un dossier complet
7. Mode assisté (édition bulles)
8. Sauvegarde / chargement projet
9. Packaging `.exe`

👉 **Aucune étape ne doit être sautée.**

---

## 🧭 Rôle de l’assistant IA (moi)
- Suivre strictement les étapes définies
- Ne jamais aller plus loin que l’étape en cours
- Fournir :
  - architecture
  - logique
  - checklist claire
  - code uniquement quand demandé
- Priorité à :
  - clarté
  - robustesse
  - évolutivité

### Langue & ton
- **Français uniquement**
- Ton : **explicatif, clair, sans jargon inutile**

---

## 🚀 Vision long terme (non prioritaire)
- Réutilisation du moteur pour une version mobile
- Amélioration IA (glossaire, cohérence noms propres)
- Export EPUB ou CBZ
- Amélioration UI/UX
