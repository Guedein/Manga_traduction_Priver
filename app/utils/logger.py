# -*- coding: utf-8 -*-
# app/utils/logger.py
"""
Systeme de logging centralise pour Manga Translator Pro
"""
import logging
import sys
from pathlib import Path
from typing import Optional


def setup_logger(
    name: str = "manga_translator",
    level: int = logging.INFO,
    log_file: Optional[Path] = None
) -> logging.Logger:
    """
    Configure et retourne un logger

    Args:
        name: Nom du logger
        level: Niveau de log (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Fichier de log optionnel

    Returns:
        Logger configure
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Eviter les doublons
    if logger.handlers:
        return logger

    # Format des messages
    formatter = logging.Formatter(
        fmt='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (optionnel)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


# Logger par defaut pour l'application
default_logger = setup_logger()


def get_logger(name: str) -> logging.Logger:
    """Recupere un logger pour un module specifique"""
    return logging.getLogger(f"manga_translator.{name}")
