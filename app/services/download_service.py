# -*- coding: utf-8 -*-
# app/services/download_service.py
from __future__ import annotations

import re
import os
import time
import base64
from pathlib import Path
from typing import List, Tuple, Optional, Callable
from urllib.parse import urlparse, urljoin

import cloudscraper
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

from app.utils.logger import get_logger

logger = get_logger("download_service")


class DownloadService:
    """Service de téléchargement d'images depuis des URLs de manga"""

    def __init__(self):
        # Utiliser cloudscraper pour contourner Cloudflare
        self.session = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'mobile': False
            }
        )
        # Session requests normale pour les téléchargements d'images (avec cookies Selenium)
        self.download_session = requests.Session()

        self.use_selenium = False  # Fallback Selenium si cloudscraper échoue
        self.selenium_cookies = []  # Cookies récupérés de la session Selenium
        self.selenium_driver = None  # Garder le driver Selenium ouvert pour télécharger les images

    def _get_page_with_selenium(self, page_url: str) -> Tuple[str, dict]:
        """
        Utilise Selenium pour contourner les CAPTCHAs Cloudflare avancés.

        Args:
            page_url: URL de la page à charger

        Returns:
            Tuple (html, cookies_dict) - Le HTML de la page et les cookies de session
        """
        chrome_options = ChromeOptions()
        # chrome_options.add_argument('--headless')  # Désactivé pour voir le CAPTCHA
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)

        # Créer le driver
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)

        try:
            driver.get(page_url)

            # Attendre que Cloudflare charge (max 30 secondes)
            logger.info("⏳ Attente de la résolution du challenge Cloudflare...")
            time.sleep(5)  # Attendre un peu pour que le challenge se charge

            # Attendre que le body contienne du contenu (pas juste le challenge)
            wait = WebDriverWait(driver, 30)
            wait.until(lambda d: len(d.find_element(By.TAG_NAME, "body").text) > 100)

            logger.info("✅ Page chargée avec succès !")
            logger.info("📜 Scroll de la page pour charger toutes les images (lazy loading)...")

            # Scroller progressivement pour charger toutes les images lazy-loaded
            last_height = driver.execute_script("return document.body.scrollHeight")
            scroll_pause = 0.5  # Pause entre chaque scroll

            while True:
                # Scroller vers le bas
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(scroll_pause)

                # Calculer la nouvelle hauteur et comparer avec l'ancienne
                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    # Si la hauteur n'a pas changé, on a atteint le bas
                    break
                last_height = new_height

            # Scroller de nouveau vers le haut pour être sûr
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(1)

            logger.info("✅ Toutes les images devraient être chargées !")

            # Récupérer le HTML de la page
            html = driver.page_source

            # Récupérer les cookies de la session Selenium (garder toutes les infos : domain, path, etc.)
            selenium_cookies = driver.get_cookies()
            logger.info(f"🍪 {len(selenium_cookies)} cookies récupérés de la session Selenium")

            # Garder le driver ouvert pour télécharger les images
            self.selenium_driver = driver
            logger.info("🔓 Driver Selenium gardé ouvert pour télécharger les images")

            return html, selenium_cookies

        except Exception as e:
            driver.quit()
            raise e

    def parse_url(self, url: str) -> Tuple[str, str]:
        """
        Parse l'URL pour extraire le nom du scan et le numéro du chapitre.

        Args:
            url: URL du chapitre (ex: https://manhuaus.com/manga/i-built-a-lifespan-club/chapter-1/)

        Returns:
            (manga_name, chapter_name)
            Ex: ("i-built-a-lifespan-club", "chapter-1")
        """
        # Parse l'URL
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.split('/') if p]

        # Chercher le pattern "manga/<nom>/<chapitre>"
        if len(path_parts) >= 2:
            # Chercher "manga" dans le path
            if 'manga' in path_parts:
                manga_idx = path_parts.index('manga')
                if manga_idx + 2 < len(path_parts):
                    manga_name = path_parts[manga_idx + 1]
                    chapter_name = path_parts[manga_idx + 2]
                    return manga_name, chapter_name

            # Fallback: prendre les deux derniers segments
            manga_name = path_parts[-2]
            chapter_name = path_parts[-1]
            return manga_name, chapter_name

        raise ValueError(f"Impossible de parser l'URL : {url}")

    def extract_image_urls(self, page_url: str) -> List[str]:
        """
        Extrait les URLs des images depuis la page du chapitre.

        Args:
            page_url: URL de la page du chapitre

        Returns:
            Liste des URLs d'images
        """
        html_content = None

        # Essayer d'abord avec cloudscraper
        try:
            parsed = urlparse(page_url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"

            headers = {
                'Referer': base_url,
            }

            response = self.session.get(page_url, headers=headers, timeout=30)
            response.raise_for_status()
            html_content = response.content.decode('utf-8', errors='ignore')

        except Exception as e:
            # Si cloudscraper échoue (403, CAPTCHA, etc.), utiliser Selenium
            logger.warning(f"⚠️ Cloudscraper a échoué ({e}), passage à Selenium...")
            try:
                html_content, self.selenium_cookies = self._get_page_with_selenium(page_url)
                self.use_selenium = True

                # IMPORTANT: Ajouter les cookies à la session de téléchargement
                if self.selenium_cookies:
                    # Ajouter chaque cookie avec toutes ses propriétés
                    for cookie in self.selenium_cookies:
                        # Récupérer le domain et s'assurer qu'il fonctionne pour les sous-domaines
                        domain = cookie.get('domain', '')

                        # Si le domain commence par un point (ex: .manhuaus.com), il est valide pour tous les sous-domaines
                        # Sinon, ajouter un point au début pour qu'il fonctionne sur les sous-domaines
                        if domain and not domain.startswith('.'):
                            domain = '.' + domain

                        self.download_session.cookies.set(
                            name=cookie['name'],
                            value=cookie['value'],
                            domain=domain,
                            path=cookie.get('path', '/')
                        )
                        logger.debug(f"  Cookie ajouté: {cookie['name']} pour domain={domain}")

                    logger.info(f"✅ {len(self.selenium_cookies)} cookies Selenium ajoutés pour domain + sous-domaines")
            except Exception as selenium_error:
                raise RuntimeError(f"Erreur lors du téléchargement de la page (cloudscraper + Selenium) : {selenium_error}")

        if not html_content:
            raise RuntimeError("Impossible de récupérer le contenu de la page")

        soup = BeautifulSoup(html_content, 'html.parser')

        # Chercher les images dans différents conteneurs possibles
        image_urls: List[str] = []

        # Stratégie 1: Chercher spécifiquement dans le conteneur de lecture (pas les pubs)
        # Pour manhuaus.com, les images du manga sont dans .reading-content ou img.page-break
        priority_selectors = [
            'img.wp-manga-chapter-img',  # Classe spécifique des pages de manga
            'div.reading-content img',
            'img.page-break',  #Images avec classe page-break directement
            'div.page-break img',
            'div.chapter-content img',
            'div.entry-content img',
            '#chapter-content img',
        ]

        # Mots-clés à exclure dans les URLs (publicités)
        ad_keywords = [
            'banner', 'ad', 'ads', 'advertisement', 'promo', 'sponsor',
            'popup', 'click', 'casino', 'cashback', 'aliexpress', 'macy',
            'shop', 'offer', 'deal', 'play-free', 'whale.io', 'mega-choco',
            'subway', 'grubhub', 'doordash', 'ubereats', 'delivery',
            'gamestop', 'game-stop', 'bc.game', 'bcgame'
        ]

        # Domaines à exclure (CDN de publicités)
        ad_domains = [
            'doubleclick.net', 'googlesyndication.com', 'googleadservices.com',
            'advertising.com', 'adnxs.com', 'ads-twitter.com',
            'quantserve.com', 'scorecardresearch.com'
        ]

        for selector in priority_selectors:
            images = soup.select(selector)
            if images:
                logger.info(f"🔍 Trouvé {len(images)} images avec le sélecteur '{selector}'")
                for img in images:
                    # NOTE: On ne filtre plus par classe/ID car c'est trop agressif
                    # (le mot 'ad' matche avec 'fade', 'loaded', etc.)
                    # On se fie uniquement au filtre d'URL qui est plus précis

                    # Chercher dans tous les attributs possibles (lazy loading)
                    src = (
                        img.get('data-src') or
                        img.get('data-lazy-src') or
                        img.get('data-original') or
                        img.get('src')
                    )

                    if not src:
                        logger.info(f"  ⚠️ Image sans src (ignorée)")
                        continue

                    logger.info(f"  🔍 Examen de l'image: {src[:80]}...")

                    # Exclure les URLs de publicités par mots-clés
                    src_lower = src.lower()
                    if any(kw in src_lower for kw in ad_keywords):
                        logger.info(f"  ❌ Ignoré (mot-clé pub): {src[:80]}...")
                        continue

                    # Exclure les URLs de publicités par domaine
                    if any(domain in src_lower for domain in ad_domains):
                        logger.info(f"  ❌ Ignoré (domaine pub): {src[:80]}...")
                        continue

                    # IMPORTANT: Ne garder QUE les images du CDN du manga (pghcdn.com, manhuaus.com)
                    parsed_src = urlparse(src)
                    allowed_domains = ['manhuaus.com', 'pghcdn.com', 'img.manhuaus.com']
                    # Si l'URL a un domaine (pas relative), vérifier qu'il est autorisé
                    if parsed_src.netloc:
                        if not any(domain in parsed_src.netloc for domain in allowed_domains):
                            logger.info(f"  ❌ Ignoré (domaine non autorisé): {src[:80]}...")
                            continue
                    # Si URL relative (pas de netloc), on l'accepte

                    # Exclure les images trop petites (souvent des pubs/icônes)
                    # Vérifier les attributs width/height si disponibles
                    width = img.get('width')
                    height = img.get('height')
                    if width and height:
                        try:
                            w = int(width)
                            h = int(height)
                            # Ignorer les images < 200px (probablement des pubs/icônes)
                            if w < 200 or h < 200:
                                logger.info(f"  ❌ Ignoré (taille trop petite): {w}x{h} - {src[:80]}...")
                                continue
                        except (ValueError, TypeError):
                            pass

                    # Convertir en URL absolue si nécessaire
                    full_url = urljoin(page_url, src)
                    if full_url not in image_urls:
                        logger.info(f"  ✅ Ajouté: {full_url[:80]}...")
                        image_urls.append(full_url)

        # Si on a trouvé des images, on les retourne
        if image_urls:
            print(f"✅ {len(image_urls)} images du manga trouvées !")
            return image_urls

        # Sinon, fallback : chercher toutes les images et filtrer
        print("⚠️ Aucune image trouvée avec les sélecteurs prioritaires, utilisation du fallback...")
        all_images = soup.find_all('img')
        for img in all_images:
            # NOTE: On ne filtre plus par classe/ID (trop agressif)
            # On se fie uniquement au filtre d'URL

            # Chercher dans tous les attributs possibles (lazy loading)
            src = (
                img.get('data-src') or
                img.get('data-lazy-src') or
                img.get('data-original') or
                img.get('src')
            )

            if src:
                src_lower = src.lower()
                if any(kw in src_lower for kw in ad_keywords):
                    continue

                if any(domain in src_lower for domain in ad_domains):
                    continue

                # Ne garder QUE les images du CDN du manga
                parsed_src = urlparse(src)
                allowed_domains = ['manhuaus.com', 'pghcdn.com', 'img.manhuaus.com']
                if parsed_src.netloc and not any(domain in parsed_src.netloc for domain in allowed_domains):
                    continue

                full_url = urljoin(page_url, src)
                if full_url not in image_urls:
                    image_urls.append(full_url)

        if not image_urls:
            raise RuntimeError("Aucune image trouvée sur la page")

        return image_urls

    def _download_image_with_selenium(self, img_url: str, output_path: Path) -> bool:
        """
        Télécharge une image en utilisant Selenium (contourne les protections Cloudflare).

        Args:
            img_url: URL de l'image
            output_path: Chemin de sortie

        Returns:
            True si succès, False sinon
        """
        if not self.selenium_driver:
            return False

        try:
            # Utiliser fetch API via JavaScript pour télécharger l'image
            script = """
            const url = arguments[0];
            return fetch(url)
                .then(response => response.blob())
                .then(blob => new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onloadend = () => resolve(reader.result);
                    reader.onerror = reject;
                    reader.readAsDataURL(blob);
                }));
            """

            # Exécuter le script et récupérer l'image en base64
            data_url = self.selenium_driver.execute_async_script(script, img_url)

            if not data_url:
                return False

            # Extraire les données base64
            header, encoded = data_url.split(',', 1)
            image_data = base64.b64decode(encoded)

            # Sauvegarder l'image
            with open(output_path, 'wb') as f:
                f.write(image_data)

            return True

        except Exception as e:
            logger.debug(f"Erreur Selenium download: {e}")
            return False

    def download_images(
        self,
        image_urls: List[str],
        output_folder: str,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        referer: Optional[str] = None
    ) -> List[str]:
        """
        Télécharge les images dans le dossier spécifié.

        Args:
            image_urls: Liste des URLs d'images à télécharger
            output_folder: Dossier de destination
            progress_callback: Callback (current, total, status_text)
            referer: URL de référence (optionnel, pour éviter les blocages)

        Returns:
            Liste des chemins des images téléchargées
        """
        # Créer le dossier
        output_path = Path(output_folder)
        output_path.mkdir(parents=True, exist_ok=True)

        downloaded_files: List[str] = []

        for idx, img_url in enumerate(image_urls, start=1):
            if progress_callback:
                progress_callback(idx, len(image_urls), f"Téléchargement image {idx}/{len(image_urls)}")

            try:
                # Déterminer l'extension depuis l'URL
                url_ext = Path(urlparse(img_url).path).suffix
                ext = url_ext if url_ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif'] else '.jpg'

                # Nom du fichier
                file_name = f"page_{idx:04d}{ext}"
                file_path = output_path / file_name

                # Si Selenium est actif, utiliser Selenium pour télécharger (contourne Cloudflare)
                if self.selenium_driver:
                    logger.debug(f"Téléchargement avec Selenium: {img_url}")
                    success = self._download_image_with_selenium(img_url, file_path)

                    if success:
                        downloaded_files.append(str(file_path))
                        logger.info(f"✅ Téléchargé ({idx}/{len(image_urls)}): {file_name}")
                        continue
                    else:
                        logger.warning(f"⚠️ Échec Selenium pour {img_url}, essai avec requests...")

                # Fallback: télécharger avec requests
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Connection': 'keep-alive',
                    'Sec-Fetch-Dest': 'image',
                    'Sec-Fetch-Mode': 'no-cors',
                    'Sec-Fetch-Site': 'same-site',
                }
                if referer:
                    headers['Referer'] = referer

                response = self.download_session.get(img_url, headers=headers, timeout=30)
                response.raise_for_status()

                # Sauvegarder
                with open(file_path, 'wb') as f:
                    f.write(response.content)

                downloaded_files.append(str(file_path))
                logger.info(f"✅ Téléchargé ({idx}/{len(image_urls)}): {file_name}")

            except Exception as e:
                print(f"⚠️ Erreur lors du téléchargement de {img_url}: {e}")
                # Continuer avec les autres images
                continue

        # Fermer le driver Selenium si présent
        if self.selenium_driver:
            try:
                logger.info("🔒 Fermeture du driver Selenium")
                self.selenium_driver.quit()
                self.selenium_driver = None
            except Exception as e:
                logger.warning(f"Erreur lors de la fermeture du driver: {e}")

        if not downloaded_files:
            raise RuntimeError("Aucune image n'a pu être téléchargée")

        return downloaded_files

    def download_chapter(
        self,
        chapter_url: str,
        base_output_dir: str,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> Tuple[str, str, List[str]]:
        """
        Télécharge un chapitre complet depuis son URL.

        Args:
            chapter_url: URL du chapitre
            base_output_dir: Dossier de base pour l'export
            progress_callback: Callback (current, total, status_text)

        Returns:
            (manga_name, chapter_name, liste_chemins_images)
        """
        # 1) Parser l'URL
        if progress_callback:
            progress_callback(0, 100, "Analyse de l'URL...")

        manga_name, chapter_name = self.parse_url(chapter_url)

        # 2) Créer le dossier de destination
        # Structure: base_output_dir/manga_name/chapter_name/
        chapter_folder = Path(base_output_dir) / manga_name / f"chapitre {chapter_name}"

        # 3) Extraire les URLs des images
        if progress_callback:
            progress_callback(10, 100, "Extraction des URLs d'images...")

        image_urls = self.extract_image_urls(chapter_url)

        # 4) Télécharger les images
        if progress_callback:
            progress_callback(20, 100, f"Téléchargement de {len(image_urls)} images...")

        downloaded_files = self.download_images(
            image_urls,
            str(chapter_folder),
            progress_callback=lambda i, t, s: progress_callback(20 + int((i/t) * 80), 100, s) if progress_callback else None,
            referer=chapter_url
        )

        if progress_callback:
            progress_callback(100, 100, "Téléchargement terminé !")

        return manga_name, chapter_name, downloaded_files
