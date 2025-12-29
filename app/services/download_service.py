# app/services/download_service.py
from __future__ import annotations

import re
import os
import time
from pathlib import Path
from typing import List, Tuple, Optional, Callable
from urllib.parse import urlparse, urljoin

import cloudscraper
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager


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
        self.use_selenium = False  # Fallback Selenium si cloudscraper échoue

    def _get_page_with_selenium(self, page_url: str) -> str:
        """
        Utilise Selenium pour contourner les CAPTCHAs Cloudflare avancés.

        Args:
            page_url: URL de la page à charger

        Returns:
            HTML de la page après résolution du CAPTCHA
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
            print("⏳ Attente de la résolution du challenge Cloudflare...")
            time.sleep(5)  # Attendre un peu pour que le challenge se charge

            # Attendre que le body contienne du contenu (pas juste le challenge)
            wait = WebDriverWait(driver, 30)
            wait.until(lambda d: len(d.find_element(By.TAG_NAME, "body").text) > 100)

            print("✅ Page chargée avec succès !")
            print("📜 Scroll de la page pour charger toutes les images (lazy loading)...")

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

            print("✅ Toutes les images devraient être chargées !")

            # Récupérer le HTML de la page
            html = driver.page_source
            return html

        finally:
            driver.quit()

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
            print(f"⚠️ Cloudscraper a échoué ({e}), passage à Selenium...")
            try:
                html_content = self._get_page_with_selenium(page_url)
                self.use_selenium = True
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
                print(f"🔍 Trouvé {len(images)} images avec le sélecteur '{selector}'")
                for img in images:
                    # Vérifier si l'image a une classe/id de pub
                    img_class = ' '.join(img.get('class', [])).lower()
                    img_id = (img.get('id') or '').lower()

                    # Exclure les images avec des classes/IDs de pub
                    if any(kw in img_class or kw in img_id for kw in ad_keywords):
                        continue

                    # Chercher dans tous les attributs possibles (lazy loading)
                    src = (
                        img.get('data-src') or
                        img.get('data-lazy-src') or
                        img.get('data-original') or
                        img.get('src')
                    )

                    if src:
                        # Exclure les URLs de publicités par mots-clés
                        src_lower = src.lower()
                        if any(kw in src_lower for kw in ad_keywords):
                            print(f"  ❌ Ignoré (mot-clé pub): {src[:80]}...")
                            continue

                        # Exclure les URLs de publicités par domaine
                        if any(domain in src_lower for domain in ad_domains):
                            print(f"  ❌ Ignoré (domaine pub): {src[:80]}...")
                            continue

                        # IMPORTANT: Ne garder QUE les images du CDN du manga (pghcdn.com, manhuaus.com)
                        parsed_src = urlparse(src)
                        allowed_domains = ['manhuaus.com', 'pghcdn.com', 'img.manhuaus.com']
                        if parsed_src.netloc and not any(domain in parsed_src.netloc for domain in allowed_domains):
                            print(f"  ❌ Ignoré (domaine non autorisé): {src[:80]}...")
                            continue

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
                                    continue
                            except (ValueError, TypeError):
                                pass

                        # Convertir en URL absolue si nécessaire
                        full_url = urljoin(page_url, src)
                        if full_url not in image_urls:
                            print(f"  ✅ Ajouté: {full_url[:80]}...")
                            image_urls.append(full_url)

        # Si on a trouvé des images, on les retourne
        if image_urls:
            print(f"✅ {len(image_urls)} images du manga trouvées !")
            return image_urls

        # Sinon, fallback : chercher toutes les images et filtrer
        print("⚠️ Aucune image trouvée avec les sélecteurs prioritaires, utilisation du fallback...")
        all_images = soup.find_all('img')
        for img in all_images:
            # Même filtrage que ci-dessus
            img_class = ' '.join(img.get('class', [])).lower()
            img_id = (img.get('id') or '').lower()

            if any(kw in img_class or kw in img_id for kw in ad_keywords):
                continue

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
                # Télécharger l'image avec Referer si fourni
                headers = {}
                if referer:
                    headers['Referer'] = referer

                response = self.session.get(img_url, headers=headers, timeout=30)
                response.raise_for_status()

                # Déterminer l'extension
                content_type = response.headers.get('content-type', '')
                if 'jpeg' in content_type or 'jpg' in content_type:
                    ext = '.jpg'
                elif 'png' in content_type:
                    ext = '.png'
                elif 'webp' in content_type:
                    ext = '.webp'
                else:
                    # Essayer d'extraire depuis l'URL
                    url_ext = Path(urlparse(img_url).path).suffix
                    ext = url_ext if url_ext in ['.jpg', '.jpeg', '.png', '.webp'] else '.jpg'

                # Nom du fichier avec padding (ex: 001.jpg, 002.jpg, ...)
                filename = f"{idx:03d}{ext}"
                file_path = output_path / filename

                # Sauvegarder
                with open(file_path, 'wb') as f:
                    f.write(response.content)

                downloaded_files.append(str(file_path))

            except Exception as e:
                print(f"⚠️ Erreur lors du téléchargement de {img_url}: {e}")
                # Continuer avec les autres images
                continue

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
