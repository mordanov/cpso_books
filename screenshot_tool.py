#!/usr/bin/env python3
"""
Скрипт для автоматического снятия скриншотов страниц учебника
с сайта russlo-edu.ru через авторизацию на edpalm-exam.online
"""

import sys
import argparse
import json
import re
import subprocess
import time
from pathlib import Path
from typing import cast

import img2pdf
from PIL import Image
from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
    ViewportSize,
)


CROP_WIDTH = 700
CROP_HEIGHT = 850


def sanitize_filename(filename: str, fallback: str) -> str:
    """Приводит имя файла к безопасному для файловой системы виду."""
    sanitized = re.sub(r'[<>:"/\\|?*]+', "_", filename).strip()
    sanitized = re.sub(r"\s+", " ", sanitized).strip(" .")
    return sanitized or fallback


def normalize_pdf_stem(pdf_stem: str, fallback: str = "book") -> str:
    """Нормализует имя PDF: удаляет переносы, непечатаемые и недопустимые символы."""
    # Убираем явные переводы строк и управляющие символы.
    normalized = pdf_stem.replace("\r", " ").replace("\n", " ")
    normalized = re.sub(r"[\x00-\x1F\x7F-\x9F]", "", normalized)

    # Убираем символы, недопустимые в имени файла на популярных ОС.
    normalized = re.sub(r'[<>:"/\\|?*]+', "_", normalized)

    # Удаляем остальные непечатаемые символы и схлопываем пробелы.
    normalized = "".join(ch for ch in normalized if ch.isprintable())
    normalized = re.sub(r"\s+", " ", normalized).strip(" .")
    return normalized or fallback


def parse_args():
    parser = argparse.ArgumentParser(
        description="Снятие скриншотов страниц учебника с russlo-edu.ru"
    )
    parser.add_argument("--username", help="Логин на edpalm-exam.online")
    parser.add_argument("--password", help="Пароль на edpalm-exam.online")
    parser.add_argument("--book", type=int, help="Номер учебника (например, 49)")
    parser.add_argument(
        "--books-config",
        type=Path,
        help="JSON-файл с username/password и словарём books для пакетного режима",
    )
    parser.add_argument(
        "--output",
        default="screenshots",
        help="Папка для сохранения скриншотов (по умолчанию: screenshots)",
    )
    parser.add_argument(
        "--start-page", default=1, type=int, help="Начальная страница (по умолчанию: 1)"
    )
    parser.add_argument(
        "--delay",
        default=1.5,
        type=float,
        help="Задержка между скриншотами в секундах (по умолчанию: 1.5)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Запуск браузера в фоновом режиме (без окна)",
    )
    parser.add_argument(
        "--viewport-width",
        default=1280,
        type=int,
        help="Ширина окна браузера (по умолчанию: 1280)",
    )
    parser.add_argument(
        "--viewport-height",
        default=900,
        type=int,
        help="Высота окна браузера (по умолчанию: 900)",
    )
    parser.add_argument(
        "--full-page",
        action="store_true",
        default=False,
        help="Снимать полную страницу (не только видимую область)",
    )
    parser.add_argument(
        "--keep-png",
        action="store_true",
        default=False,
        help="Не удалять PNG после сборки PDF",
    )
    return parser.parse_args()


def load_books_config(config_path: Path):
    """Загружает конфигурацию пакетного режима из JSON."""
    if not config_path.exists():
        raise RuntimeError(f"Файл конфигурации не найден: {config_path}")

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Некорректный JSON в {config_path}: {e}")

    username = str(raw.get("username", "")).strip()
    password = str(raw.get("password", "")).strip()
    books = raw.get("books")

    if not username:
        raise RuntimeError("В JSON отсутствует непустое поле username.")
    if not password:
        raise RuntimeError("В JSON отсутствует непустое поле password.")
    if not isinstance(books, dict) or not books:
        raise RuntimeError("В JSON поле books должно быть непустым словарём.")

    book_ids = []
    for key in books.keys():
        try:
            book_ids.append(int(key))
        except (TypeError, ValueError):
            raise RuntimeError(f"Некорректный ключ книги в books: {key!r}. Ожидается число.")

    return username, password, sorted(set(book_ids))


def start_sleep_prevention():
    """Запускает caffeinate на macOS, чтобы не допустить сон/блокировку во время цикла."""
    if sys.platform != "darwin":
        return None

    try:
        # -d: display sleep off, -i: idle sleep off, -m: disk sleep off, -u/-s: user/system active
        return subprocess.Popen(["caffeinate", "-dimsu"])
    except FileNotFoundError:
        print("⚠️  'caffeinate' не найден. Защита от сна не включена.")
        return None


def stop_sleep_prevention(process):
    """Останавливает ранее запущенный процесс caffeinate."""
    if not process:
        return

    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()


def login(page, username: str, password: str):
    """Авторизация на edpalm-exam.online"""
    print("[1/5] Открываем страницу входа...")
    page.goto("https://edpalm-exam.online/login/index.php", wait_until="networkidle")

    print("[2/5] Вводим учётные данные...")
    page.fill("#username", username)
    page.fill("#password", password)
    page.click("#loginbtn")

    # Ждём перехода после логина
    page.wait_for_load_state("networkidle")

    # Проверяем успешность входа
    if "login" in page.url.lower() and "index" in page.url.lower():
        # Попробуем найти сообщение об ошибке
        error = page.query_selector(".loginerrors, #loginerrormessage, .alert")
        if error:
            raise RuntimeError(f"Ошибка авторизации: {error.inner_text().strip()}")
        raise RuntimeError("Не удалось войти — остались на странице логина.")

    print(f"    ✓ Авторизован. Текущий URL: {page.url}")


def open_book_page(page, book_number: int):
    """Переход к странице учебника через промежуточный сайт"""
    print("[3/8] Переходим к странице курса...")
    page.goto(
        "https://edpalm-exam.online/mod/page/view.php?id=14124",
        wait_until="networkidle",
    )

    print("[4/8] Нажимаем кнопку 'Открыть пособие. Часть 1'...")
    # Ищем кнопку/ссылку с нужным текстом
    button = page.get_by_text("Открыть пособие. Часть 1", exact=False).first
    if not button:
        raise RuntimeError("Кнопка 'Открыть пособие. Часть 1' не найдена на странице.")

    # Открываем в новой вкладке (перехватываем новую вкладку если откроется)
    with page.context.expect_page() as new_page_info:
        button.click()
    new_page = new_page_info.value
    new_page.wait_for_load_state("networkidle")
    print(f"    ✓ Открылась страница: {new_page.url}")

    print("[5/8] Ищем кнопку 'Вернуться в Библиотеку РС' и кликаем...")
    back_to_library = new_page.locator('img[alt="Вернуться в Библиотеку РС"]').first
    try:
        back_to_library.wait_for(state="visible", timeout=7000)
    except PlaywrightTimeoutError:
        raise RuntimeError(
            "Картинка 'Вернуться в Библиотеку РС' не найдена на странице."
        )
    back_to_library.click()
    new_page.wait_for_load_state("networkidle")

    print(f"[6/8] Ищем раздел учебника data-catid={book_number} и кликаем...")
    book_tile = new_page.locator(f'div[data-catid="{book_number}"]').first
    try:
        book_tile.wait_for(state="visible", timeout=7000)
    except PlaywrightTimeoutError:
        raise RuntimeError(
            f'Элемент div[data-catid="{book_number}"] не найден на странице.'
        )

    print("[7/8] Получаем название книги для имени итогового PDF...")
    book_name_element = book_tile.locator('div[class="bookName"]').first
    try:
        book_name_element.wait_for(state="attached", timeout=7000)
    except PlaywrightTimeoutError:
        raise RuntimeError(
            'Внутри div[data-catid] не найден вложенный div[tag="bookName"].'
        )

    raw_book_name = book_name_element.get_attribute("value")
    if not raw_book_name:
        raw_book_name = (book_name_element.text_content() or "").strip()

    final_pdf_stem = sanitize_filename(raw_book_name, fallback=f"book_{book_number}")
    print(f"    ✓ Имя итогового файла: {final_pdf_stem}.pdf")

    book_tile.click()
    new_page.wait_for_load_state("networkidle")

    # Переходим к обложке учебника
    print("[8/8] Открываем обложку учебника...")
    cover_url = f"https://russlo-edu.ru/reader/books/{book_number}/contents/cover.php"
    new_page.goto(cover_url, wait_until="networkidle")
    print(f"    ✓ Обложка загружена: {new_page.url}")

    return new_page, final_pdf_stem


def save_center_cropped_screenshot(
    page,
    filepath: Path,
    full_page: bool,
    crop_width: int = CROP_WIDTH,
    crop_height: int = CROP_HEIGHT,
):
    """Сохраняет скриншот и обрезает его по центру до фиксированного размера."""
    page.screenshot(path=str(filepath), full_page=full_page)

    with Image.open(filepath) as image:
        width, height = image.size
        if width < crop_width or height < crop_height:
            raise RuntimeError(
                f"Скриншот слишком мал для обрезки до {crop_width}x{crop_height}: "
                f"получено {width}x{height}."
            )

        left = (width - crop_width) // 2
        top = (height - crop_height) // 2
        right = left + crop_width
        bottom = top + crop_height

        cropped = image.crop((left, top, right, bottom))
        cropped.save(filepath)


def build_pdf_and_cleanup(
    output_dir: Path, pdf_stem: str, keep_png: bool = False
) -> Path:
    """Собирает все PNG в один PDF и при необходимости удаляет исходные PNG файлы."""
    png_files = sorted(output_dir.glob("*.png"))
    if not png_files:
        raise RuntimeError("PNG файлы для сборки PDF не найдены.")

    normalized_stem = normalize_pdf_stem(pdf_stem, fallback="book")
    pdf_path = output_dir / f"{normalized_stem}.pdf"
    png_paths = [str(path) for path in png_files]

    with open(pdf_path, "wb") as pdf_file:
        pdf_file.write(img2pdf.convert(png_paths))

    if not keep_png:
        for png_file in png_files:
            png_file.unlink(missing_ok=True)

    return pdf_path


def take_screenshots(
    page,
    book_number: int,
    output_dir: Path,
    start_page: int,
    delay: float,
    full_page: bool,
):
    """Основной цикл снятия скриншотов"""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Скриншот обложки
    cover_path = output_dir / "cover.png"
    save_center_cropped_screenshot(page, cover_path, full_page=full_page)
    print(f"    📸 cover.png  [{CROP_WIDTH}x{CROP_HEIGHT}, центр]")

    page_num = start_page
    total = 0

    print(
        f"\nНачинаем снимать страницы учебника №{book_number} начиная со страницы {start_page}..."
    )
    print("-" * 50)

    while True:
        page_str = f"{page_num:03d}"
        url = f"https://russlo-edu.ru/reader/books/{book_number}/contents/page{page_str}.php"

        # Переходим на страницу
        response = page.goto(url, wait_until="domcontentloaded")

        # Проверяем HTTP статус
        if response and response.status == 404:
            print(f"\n✅ Страница {page_str} вернула 404 — учебник завершён.")
            print(f"   Всего снято скриншотов: {total} (+ обложка)")
            break

        if response and response.status >= 400:
            print(
                f"\n⚠️  Страница {page_str} вернула статус {response.status}. Останавливаемся."
            )
            break

        # Небольшая пауза для полной загрузки контента
        time.sleep(delay)

        # Снимаем скриншот
        filename = f"page{page_str}.png"
        filepath = output_dir / filename
        save_center_cropped_screenshot(page, filepath, full_page=full_page)
        print(f"    📸 {filename}  [{url}] [{CROP_WIDTH}x{CROP_HEIGHT}, центр]")

        total += 1
        page_num += 1

    return total


def process_one_book(
    browser,
    username: str,
    password: str,
    book_number: int,
    args,
) -> Path:
    """Выполняет полный сценарий для одного учебника и возвращает путь к PDF."""
    output_dir = Path(args.output) / f"book_{book_number}"
    print(f"\n{'='*55}")
    print(f"  Учебник №{book_number}")
    print(f"  Выходная папка: {output_dir.resolve()}")
    print(f"  Начальная страница: {args.start_page}")
    print(f"  Задержка: {args.delay}с | Полная страница: {args.full_page}")
    print(f"  Обрезка скриншота: {CROP_WIDTH}x{CROP_HEIGHT} (центр)")
    print(f"  Сохранять PNG после PDF: {args.keep_png}")
    print(f"{'='*55}\n")

    context = browser.new_context(
        viewport=cast(
            ViewportSize,
            cast(
                object,
                {"width": args.viewport_width, "height": args.viewport_height},
            ),
        ),
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        accept_downloads=True,
    )

    page = context.new_page()
    try:
        # Шаг 1-2: Логин
        login(page, username, password)

        # Шаги 3-8: Открыть страницу учебника
        reader_page, final_pdf_stem = open_book_page(page, book_number)

        # Шаг 9: Цикл скриншотов
        pages_total = take_screenshots(
            reader_page,
            book_number=book_number,
            output_dir=output_dir,
            start_page=args.start_page,
            delay=args.delay,
            full_page=args.full_page,
        )

        # Шаг 10: Сборка PDF и опциональная очистка PNG
        if args.keep_png:
            print("\n[10/10] Собираем PDF (PNG сохраняются)...")
        else:
            print("\n[10/10] Собираем PDF и удаляем PNG файлы...")

        pdf_path = build_pdf_and_cleanup(output_dir, final_pdf_stem, keep_png=args.keep_png)
        print(f"    ✓ PDF собран: {pdf_path.name}")
        if args.keep_png:
            print(f"    ✓ PNG сохранены: {pages_total + 1}")
        else:
            print(f"    ✓ Удалено PNG файлов: {pages_total + 1}")

        print(f"\n✅ Готово. Итоговый PDF: {pdf_path.resolve()}")
        return pdf_path
    finally:
        context.close()


def main():
    args = parse_args()

    if args.books_config:
        username, password, book_ids = load_books_config(args.books_config)
    else:
        if not args.username or not args.password or args.book is None:
            raise RuntimeError(
                "Для одиночного режима нужно передать --username --password --book, "
                "или использовать --books-config для пакетного запуска."
            )
        username, password, book_ids = args.username, args.password, [args.book]

    with sync_playwright() as p:
        sleep_guard = start_sleep_prevention()
        if sleep_guard:
            print("🟢 Защита от сна включена (caffeinate).")

        browser = p.chromium.launch(
            headless=args.headless, args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        success = 0
        failed = []
        try:
            if args.books_config:
                print(f"\n📚 Пакетный режим: найдено книг в JSON: {len(book_ids)}")

            for book_number in book_ids:
                try:
                    process_one_book(
                        browser=browser,
                        username=username,
                        password=password,
                        book_number=book_number,
                        args=args,
                    )
                    success += 1
                except (PlaywrightTimeoutError, RuntimeError) as e:
                    failed.append((book_number, str(e)))
                    print(f"\n❌ Книга {book_number}: {e}", file=sys.stderr)

            if failed:
                print(f"\n⚠️  Завершено с ошибками: успешно {success}, с ошибками {len(failed)}")
                for book_number, message in failed:
                    print(f"   - {book_number}: {message}", file=sys.stderr)
                sys.exit(1)

            print(f"\n✅ Успешно обработано книг: {success}")

        except PlaywrightTimeoutError as e:
            print(f"\n❌ Таймаут: {e}", file=sys.stderr)
            sys.exit(1)
        except RuntimeError as e:
            print(f"\n❌ Ошибка: {e}", file=sys.stderr)
            sys.exit(1)
        except KeyboardInterrupt:
            print("\n⚠️  Прервано пользователем.")
        finally:
            browser.close()
            stop_sleep_prevention(sleep_guard)
            if sleep_guard:
                print("⚪ Защита от сна отключена.")



if __name__ == "__main__":
    main()
