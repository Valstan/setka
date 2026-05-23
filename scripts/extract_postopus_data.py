#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Улучшенный скрипт для извлечения данных из старой MongoDB базы Postopus
Извлекает: регионы, сообщества VK, фильтры, расписания
"""
import json
import os

# Подключение к старой БД
# Import from config
import sys
from datetime import datetime

import pymongo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.runtime import MONGO_CONNECTION  # noqa: E402

MONGO_URI = MONGO_CONNECTION["uri"]

OUTPUT_DIR = "/home/valstan/SETKA/old_project_analysis"


def connect_to_mongodb():
    """Подключение к MongoDB"""
    print("🔌 Подключение к MongoDB...")
    try:
        client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.server_info()  # Проверка подключения
        db = client["postopus"]
        print("✅ Подключение успешно!")
        return client, db
    except Exception as e:
        print(f"❌ Ошибка подключения: {e}")
        return None, None


def extract_regions_config(db):
    """Извлечь конфигурации всех регионов"""
    print("\n📍 Извлечение конфигураций регионов...")

    regions_data = {}
    collections = db.list_collection_names()

    # Список региональных коллекций (не служебных)
    regional_collections = [
        c for c in collections if c not in ["config", "malmigrus", "afon", "system.indexes"]
    ]

    print(f"Найдено региональных коллекций: {len(regional_collections)}")

    for coll_name in regional_collections:
        coll = db[coll_name]

        # Найти документ конфигурации
        config_doc = coll.find_one({"title": "config"})

        if config_doc:
            print(f"  ✅ {coll_name}: конфигурация найдена")

            regions_data[coll_name] = {
                "code": coll_name,
                "name_group": config_doc.get("name_group", ""),
                "post_group_vk": config_doc.get("post_group_vk"),
                "post_group_telega": config_doc.get("post_group_telega", ""),
                "neighbors": config_doc.get("sosed", ""),
                "heshteg_local": config_doc.get("heshteg_local", ""),
                "filter_region": config_doc.get("filter_region", {}),
                "communities": extract_communities_from_config(config_doc),
                "schedules": extract_schedules(coll_name, coll),
            }
        else:
            print(f"  ⚠️  {coll_name}: конфигурация не найдена")

    print(f"\n✅ Извлечено регионов: {len(regions_data)}")
    return regions_data


def extract_communities_from_config(config_doc):
    """Извлечь сообщества из конфигурации региона"""
    communities = []

    # Категории сообществ
    categories = [
        "admin",
        "novost",
        "kultura",
        "sport",
        "detsad",
        "union",
        "reklama",
        "prikol",
        "krugozor",
        "music",
        "art",
        "kino",
        "sosed",
    ]

    for category in categories:
        if category in config_doc:
            category_data = config_doc[category]

            # Это может быть словарь {название: vk_id}
            if isinstance(category_data, dict):
                for name, vk_id in category_data.items():
                    if isinstance(vk_id, (int, str)) and vk_id:
                        try:
                            # Преобразовать строку в int если нужно
                            if isinstance(vk_id, str):
                                vk_id = int(vk_id)

                            communities.append(
                                {
                                    "vk_id": vk_id,
                                    "name": clean_community_name(name),
                                    "category": category,
                                }
                            )
                        except (ValueError, TypeError):
                            continue

            # Или список ID
            elif isinstance(category_data, list):
                for vk_id in category_data:
                    if isinstance(vk_id, int):
                        communities.append(
                            {"vk_id": vk_id, "name": f"Community {vk_id}", "category": category}
                        )

    return communities


def clean_community_name(name):
    """Очистить название сообщества от префиксов"""
    # Убрать префиксы "в ", "на ", etc.
    prefixes = ["в ", "на ", "из ", "для ", "с "]
    name_clean = name
    for prefix in prefixes:
        if name_clean.startswith(prefix):
            name_clean = name_clean[len(prefix) :]
    return name_clean.strip()


def extract_schedules(region_code, collection):
    """Извлечь расписания публикаций для региона"""
    schedules = []

    # Попробовать найти документы с расписаниями
    schedule_fields = ["CRON_SCHEDULE", "schedule", "cron"]

    for field in schedule_fields:
        doc = collection.find_one({field: {"$exists": True}})
        if doc and field in doc:
            schedule_data = doc[field]

            if isinstance(schedule_data, dict):
                for task_name, cron_expr in schedule_data.items():
                    schedules.append(
                        {
                            "region_code": region_code,
                            "task_name": task_name,
                            "cron_expression": cron_expr,
                        }
                    )
            elif isinstance(schedule_data, list):
                schedules.extend(schedule_data)

    return schedules


def extract_global_filters(db):
    """Извлечь глобальные фильтры из config коллекции"""
    print("\n🔍 Извлечение глобальных фильтров...")

    filters_data = {
        "blacklist_delete": [],
        "blacklist_clear": [],
        "blacklist_fast": [],
        "black_ids": [],
        "bad_name_groups": [],
        "only_main_news": [],
        "kirov_words": [],
        "tatar_words": [],
    }

    config_coll = db["config"]
    config_doc = config_coll.find_one({"title": "config"})

    if not config_doc:
        print("  ⚠️  Глобальная конфигурация не найдена")
        return filters_data

    # Извлечь черные списки слов
    if "delete_msg_blacklist" in config_doc:
        filters_data["blacklist_delete"] = config_doc["delete_msg_blacklist"]
        print(f"  ✅ delete_msg_blacklist: {len(filters_data['blacklist_delete'])} слов")

    if "clear_text_blacklist" in config_doc:
        filters_data["blacklist_clear"] = config_doc["clear_text_blacklist"]
        print(f"  ✅ clear_text_blacklist: {len(filters_data['blacklist_clear'])} слов")

    if "fast_del_msg_blacklist" in config_doc:
        filters_data["blacklist_fast"] = config_doc["fast_del_msg_blacklist"]
        print(f"  ✅ fast_del_msg_blacklist: {len(filters_data['blacklist_fast'])} слов")

    # Черные списки ID
    if "black_id" in config_doc:
        filters_data["black_ids"] = config_doc["black_id"]
        print(f"  ✅ black_id: {len(filters_data['black_ids'])} ID")

    # Плохие названия групп
    if "bad_name_group" in config_doc:
        filters_data["bad_name_groups"] = config_doc["bad_name_group"]
        print(f"  ✅ bad_name_group: {len(filters_data['bad_name_groups'])} названий")

    # Группы только от админов
    if "only_main_news" in config_doc:
        filters_data["only_main_news"] = config_doc["only_main_news"]
        print(f"  ✅ only_main_news: {len(filters_data['only_main_news'])} групп")

    # Региональные слова
    if "kirov_words" in config_doc:
        filters_data["kirov_words"] = config_doc["kirov_words"]
        print(f"  ✅ kirov_words: {len(filters_data['kirov_words'])} слов")

    if "tatar_words" in config_doc:
        filters_data["tatar_words"] = config_doc["tatar_words"]
        print(f"  ✅ tatar_words: {len(filters_data['tatar_words'])} слов")

    print("\n✅ Извлечены глобальные фильтры")
    return filters_data


def save_json(data, filename):
    """Сохранить данные в JSON"""
    filepath = f"{OUTPUT_DIR}/{filename}"

    # Конвертация ObjectId в строки
    def convert_objectid(obj):
        if isinstance(obj, dict):
            return {k: convert_objectid(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_objectid(item) for item in obj]
        elif hasattr(obj, "__str__") and type(obj).__name__ == "ObjectId":
            return str(obj)
        else:
            return obj

    data_converted = convert_objectid(data)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data_converted, f, ensure_ascii=False, indent=2, default=str)

    print(f"💾 Сохранено: {filepath}")


def generate_statistics(regions_data, filters_data):
    """Сгенерировать статистику"""
    print("\n📊 Генерация статистики...")

    total_communities = sum(len(r["communities"]) for r in regions_data.values())

    stats = {
        "extraction_date": datetime.now().isoformat(),
        "total_regions": len(regions_data),
        "total_communities": total_communities,
        "total_filters": {
            "blacklist_delete": len(filters_data["blacklist_delete"]),
            "blacklist_clear": len(filters_data["blacklist_clear"]),
            "blacklist_fast": len(filters_data["blacklist_fast"]),
            "black_ids": len(filters_data["black_ids"]),
            "bad_name_groups": len(filters_data["bad_name_groups"]),
            "only_main_news": len(filters_data["only_main_news"]),
            "kirov_words": len(filters_data["kirov_words"]),
            "tatar_words": len(filters_data["tatar_words"]),
        },
        "regions_breakdown": {},
    }

    for region_code, region_data in regions_data.items():
        stats["regions_breakdown"][region_code] = {
            "name": region_data["name_group"],
            "communities_count": len(region_data["communities"]),
            "schedules_count": len(region_data["schedules"]),
        }

    return stats


def main():
    print("=" * 70)
    print("🚀 Извлечение данных из старой базы Postopus (улучшенная версия)")
    print("=" * 70)

    client, db = connect_to_mongodb()

    if db is None:
        print("❌ Не удалось подключиться к базе данных")
        return 1

    try:
        # Извлечь все данные
        regions_data = extract_regions_config(db)
        filters_data = extract_global_filters(db)
        stats = generate_statistics(regions_data, filters_data)

        # Сохранить данные
        save_json(regions_data, "postopus_regions.json")
        save_json(filters_data, "postopus_filters.json")
        save_json(stats, "postopus_stats.json")

        print("\n" + "=" * 70)
        print("✅ Извлечение данных завершено успешно!")
        print("=" * 70)
        print("\n📊 Статистика:")
        print(f"  • Регионов: {stats['total_regions']}")
        print(f"  • Сообществ VK: {stats['total_communities']}")
        print(f"  • Фильтров слов (delete): {stats['total_filters']['blacklist_delete']}")
        print(f"  • Фильтров ID: {stats['total_filters']['black_ids']}")

        return 0

    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        if client:
            client.close()
            print("\n🔌 Соединение закрыто")


if __name__ == "__main__":
    import sys

    sys.exit(main())
