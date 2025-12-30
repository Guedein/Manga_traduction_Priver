# -*- coding: utf-8 -*-
# app/services/translate_service.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Tuple

import time
import requests

from app.utils.logger import get_logger

logger = get_logger("translate_service")


TranslatorMode = Literal["online", "local"]


@dataclass
class TranslateSettings:
    mode: TranslatorMode = "online"
    api_key: str = ""
    provider: str = "deepl"
    src_lang: str = "EN"
    tgt_lang: str = "FR"
    auto_fallback_to_local: bool = True


class TranslateError(RuntimeError):
    pass


class OnlineTranslator:
    """
    Online via DeepL API.
    """

    def __init__(self) -> None:
        # API Free (tu peux remplacer par api.deepl.com si plan pro)
        self.base_url = "https://api-free.deepl.com/v2/translate"

    def translate_many(self, texts: List[str], src_lang: str, tgt_lang: str, api_key: str) -> List[str]:
        if not api_key.strip():
            raise TranslateError("Clé API manquante (DeepL).")

        data = []
        for t in texts:
            data.append(("text", t))
        data.append(("source_lang", src_lang.upper()))
        data.append(("target_lang", tgt_lang.upper()))

        headers = {"Authorization": f"DeepL-Auth-Key {api_key.strip()}"}

        r = requests.post(self.base_url, data=data, headers=headers, timeout=30)
        if r.status_code != 200:
            raise TranslateError(f"DeepL erreur {r.status_code}: {r.text[:200]}")

        payload = r.json()
        translations = payload.get("translations", [])
        out: List[str] = []
        for tr in translations:
            out.append(str(tr.get("text", "")))
        return out


class LocalTranslator:
    """
    Offline via HuggingFace Transformers (MarianMT).
    EN -> FR pour commencer (simple et efficace).
    """

    def __init__(self) -> None:
        self._loaded = False
        self._tokenizer = None
        self._model = None
        self._model_name: Optional[str] = None

    def _ensure_loaded(self, src_lang: str, tgt_lang: str) -> None:
        if self._loaded:
            return

        src = src_lang.upper()
        tgt = tgt_lang.upper()

        if not (src == "EN" and tgt == "FR"):
            raise TranslateError(f"Local: pair non supportée pour l’instant ({src}->{tgt}).")

        # ✅ import "lazy" (évite les erreurs au lancement si pas installé)
        try:
            from transformers import MarianMTModel, MarianTokenizer
        except ModuleNotFoundError as e:
            raise TranslateError(
                "Mode Local indisponible : installe 'transformers sentencepiece torch'"
            ) from e

        model_name = "Helsinki-NLP/opus-mt-en-fr"
        self._tokenizer = MarianTokenizer.from_pretrained(model_name)
        self._model = MarianMTModel.from_pretrained(model_name)
        self._model_name = model_name
        self._loaded = True

    def translate_many(self, texts: List[str], src_lang: str, tgt_lang: str) -> List[str]:
        self._ensure_loaded(src_lang, tgt_lang)

        assert self._tokenizer is not None
        assert self._model is not None

        batch = self._tokenizer(texts, return_tensors="pt", padding=True, truncation=True)
        gen = self._model.generate(**batch, max_new_tokens=256)
        out = self._tokenizer.batch_decode(gen, skip_special_tokens=True)
        return [o.strip() for o in out]


class TranslateService:
    def __init__(self) -> None:
        self.settings = TranslateSettings()
        self._online = OnlineTranslator()
        self._local = LocalTranslator()

        # Cache des traductions {(src_lang, tgt_lang, text): translation}
        self._translation_cache: Dict[Tuple[str, str, str], str] = {}
        self._cache_max_size = 1000  # Limite pour éviter fuite mémoire

    def set_settings(
        self,
        mode: TranslatorMode,
        api_key: str,
        src_lang: str,
        tgt_lang: str,
        auto_fallback_to_local: bool = True,
        provider: str = "deepl",
    ) -> None:
        self.settings.mode = mode
        self.settings.api_key = api_key or ""
        self.settings.src_lang = (src_lang or "EN").upper()
        self.settings.tgt_lang = (tgt_lang or "FR").upper()
        self.settings.auto_fallback_to_local = auto_fallback_to_local
        self.settings.provider = provider

    def translate_many(self, texts: List[str]) -> List[str]:
        texts = [t.strip() for t in texts if t and t.strip()]
        if not texts:
            return []

        s = self.settings

        # ✅ Vérifier le cache pour chaque texte
        cached_results: List[Optional[str]] = []
        texts_to_translate: List[str] = []
        indices_to_translate: List[int] = []

        for i, text in enumerate(texts):
            cache_key = (s.src_lang, s.tgt_lang, text)
            if cache_key in self._translation_cache:
                cached_results.append(self._translation_cache[cache_key])
            else:
                cached_results.append(None)
                texts_to_translate.append(text)
                indices_to_translate.append(i)

        # Si tout est en cache, retourner directement
        if not texts_to_translate:
            logger.info(f"✅ Cache hit : {len(texts)} traductions récupérées du cache")
            return [r for r in cached_results if r is not None]

        logger.debug(f"📊 Cache : {len(cached_results) - len(texts_to_translate)}/{len(texts)} hits, {len(texts_to_translate)} à traduire")

        # si online mais pas de clé ET pas de fallback → erreur
        if s.mode == "online" and not s.api_key.strip() and not s.auto_fallback_to_local:
            raise TranslateError("Mode Online choisi mais clé API vide. Ajoute une clé DeepL ou passe en Local.")

        t0 = time.perf_counter()

        try:
            if s.mode == "online":
                new_translations = self._online.translate_many(
                    texts=texts_to_translate,
                    src_lang=s.src_lang,
                    tgt_lang=s.tgt_lang,
                    api_key=s.api_key,
                )
            else:
                new_translations = self._local.translate_many(
                    texts=texts_to_translate,
                    src_lang=s.src_lang,
                    tgt_lang=s.tgt_lang,
                )
        except Exception as e:
            # ✅ fallback auto si Online fail
            if s.mode == "online" and s.auto_fallback_to_local:
                logger.warning(f"⚠️ Traduction Online échouée ({e})")
                logger.info("🔄 Basculement automatique vers traduction Local...")
                new_translations = self._local.translate_many(texts_to_translate, s.src_lang, s.tgt_lang)
            else:
                raise

        _dt = time.perf_counter() - t0

        # ✅ Mettre en cache les nouvelles traductions
        for text, translation in zip(texts_to_translate, new_translations):
            cache_key = (s.src_lang, s.tgt_lang, text)

            # LRU eviction si cache plein
            if len(self._translation_cache) >= self._cache_max_size:
                # Supprimer la plus ancienne entrée (FIFO)
                oldest_key = next(iter(self._translation_cache))
                del self._translation_cache[oldest_key]

            self._translation_cache[cache_key] = translation

        # ✅ Reconstituer la liste complète (cache + nouvelles)
        result: List[str] = []
        new_idx = 0
        for i in range(len(texts)):
            if cached_results[i] is not None:
                result.append(cached_results[i])  # type: ignore
            else:
                result.append(new_translations[new_idx])
                new_idx += 1

        return result

    def clear_cache(self) -> None:
        """Vide le cache des traductions"""
        old_size = len(self._translation_cache)
        self._translation_cache.clear()
        logger.info(f"🗑️ Cache vidé : {old_size} traductions supprimées")

    def get_cache_stats(self) -> Dict[str, int]:
        """Retourne les statistiques du cache"""
        return {
            "size": len(self._translation_cache),
            "max_size": self._cache_max_size,
        }
