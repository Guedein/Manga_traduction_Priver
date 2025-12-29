print("🔍 Test des dépendances...\n")

def check(lib, name=None):
    try:
        __import__(lib)
        print(f"✅ {name or lib} : OK")
    except Exception as e:
        print(f"❌ {name or lib} : ERREUR -> {e}")

check("PySide6", "PySide6 (UI)")
check("PIL", "Pillow (images)")
check("cv2", "OpenCV")
check("numpy", "NumPy")
check("paddleocr", "PaddleOCR")
check("paddle", "PaddlePaddle")
check("reportlab", "ReportLab")
check("openai", "OpenAI")
check("dotenv", "python-dotenv")

print("\n🎉 Test terminé")
