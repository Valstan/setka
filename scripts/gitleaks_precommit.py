#!/usr/bin/env python3
"""Локальный секрет-сканер на staged-изменениях. Обёртка над бинарником gitleaks.

**Почему обёртка, а не штатный хук gitleaks.** Штатный хук в pre-commit —
``language: golang``, то есть требует Go-тулчейн на машине разработчика. Прибитие
линтеров к недоступному локально окружению уже дважды ломало pre-commit владельцу
(инциденты 2026-05-22 и 2026-05-23, см. шапку ``.pre-commit-config.yaml``), а на
Windows Go не стоит.

**Почему пропуск, а не падение, когда бинарника нет.** Гейт на мерже — это CI,
где gitleaks ставится всегда и обязателен для влития. Этот хук — раннее
предупреждение: поймать секрет до push, а не после. Если бы он падал на машине
без бинарника, то ломал бы commit там, где защита и так есть, — ровно та ошибка,
что дважды ломала pre-commit. Поэтому пропуск, а не падение.

**Пропуск задумывался «громким», и это оказалось неправдой (замер 2026-08-25).**
Предупреждение печатается в stderr, но ``pre-commit`` показывает вывод только
УПАВШИХ хуков: и при честной проверке, и при пропуске в терминале одинаковое
``secret scan (staged)....Passed``. То есть по выводу отличить «проверено» от «не
проверялось» нельзя — ровно то, чего этот абзац обещал не допустить. Починить в
хуке нечем (режима «показать вывод успешного хука» у pre-commit нет), поэтому
здесь — честная формулировка вместо обещания: **если бинарника нет, эта строка
«Passed» не значит ничего.** Проверять наличие — командой ниже.

Переносимое: громкость — свойство не источника, а канала. Отказ, напечатанный в
поток, который потребитель показывает только при падении, является молчаливым.

Проверить, что бинарник на месте (и версия совпадает с CI):

    python scripts/gitleaks_precommit.py --which

Установка бинарника (Windows, та же версия 8.21.2, что прибита в CI): взять
``gitleaks_8.21.2_windows_x64.zip`` из релизов ``github.com/gitleaks/gitleaks``
и распаковать ``gitleaks.exe`` в ``%USERPROFILE%\\.local\\bin``.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = REPO_ROOT / ".gitleaks.toml"

# Куда смотреть помимо PATH: каталог, в который бинарник кладут на машинах
# владельца. Список, а не одно место, — на разных ОС каталог разный.
EXTRA_LOCATIONS = (
    pathlib.Path.home() / ".local" / "bin" / "gitleaks.exe",
    pathlib.Path.home() / ".local" / "bin" / "gitleaks",
    pathlib.Path("/usr/local/bin/gitleaks"),
)


def find_gitleaks() -> str | None:
    found = shutil.which("gitleaks")
    if found:
        return found
    for candidate in EXTRA_LOCATIONS:
        if candidate.is_file():
            return str(candidate)
    return None


def main() -> int:
    binary = find_gitleaks()

    # `--which` существует ровно потому, что по строке «Passed» в pre-commit
    # отличить проверку от пропуска НЕЛЬЗЯ (см. шапку). Это единственный способ
    # узнать, работает ли локальный гейт вообще.
    if "--which" in sys.argv[1:]:
        if binary is None:
            print("gitleaks: НЕ НАЙДЕН — локальная проверка секретов не работает")
            return 1
        print(f"gitleaks: {binary}", flush=True)  # flush: иначе версия из
        # подпроцесса печатается РАНЬШЕ пути и вывод читается наоборот
        return subprocess.run([binary, "version"]).returncode

    if binary is None:
        print(
            "gitleaks: бинарник не найден — локальная проверка секретов ПРОПУЩЕНА.\n"
            "          Гейт на мерже (CI) работает независимо и остаётся обязательным.\n"
            "          Поставить локально: см. шапку scripts/gitleaks_precommit.py",
            file=sys.stderr,
        )
        return 0

    result = subprocess.run(
        [
            binary,
            "git",
            "--pre-commit",
            "--staged",
            "--config",
            str(CONFIG),
            "--redact",  # без него найденный секрет печатается в вывод хука
            "--no-banner",
            "--exit-code",
            "1",
        ],
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        print(
            "\ngitleaks: в staged-изменениях найден секрет (значение скрыто --redact).\n"
            "          Убрать значение из кода и положить в окружение/комнату КАРМАНа.\n"
            "          Если это заведомо синтетическая фикстура — завести её в allowlist\n"
            "          .gitleaks.toml, а не обходить хук через --no-verify.",
            file=sys.stderr,
        )
    return result.returncode


if __name__ == "__main__":
    os.environ.setdefault("GITLEAKS_CONFIG", str(CONFIG))
    sys.exit(main())
