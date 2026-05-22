#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для извлечения данных из старой MongoDB базы Postopus
"""
import json
from datetime import datetime

import pymongo

# Подключение к старой БД
# Import from config
from config.runtime import MONGO_CONNECTION

MONGO_URI = MONGO_CONNECTION["uri"]


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


def extract_communities(db):
    """Извлечь списки сообществ VK"""
    print("\n📊 Извлечение сообществ VK...")
    communities = []

    try:
        collections = db.list_collection_names()
        print(f"Найдено коллекций: {len(collections)}")

        # Ищем коллекции с сообществами
        for coll_name in collections:
            if "communit" in coll_name.lower() or "group" in coll_name.lower():
                coll = db[coll_name]
                count = coll.count_documents({})
                print(f"  - {coll_name}: {count} записей")

                for doc in coll.find():
                    communities.append({"collection": coll_name, "data": doc})

        print(f"✅ Извлечено сообществ: {len(communities)}")
        return communities
    except Exception as e:
        print(f"❌ Ошибка извлечения сообществ: {e}")
        return []


def extract_posts(db):
    """Извлечь посты"""
    print("\n📝 Извлечение постов...")
    posts = []

    try:
        collections = db.list_collection_names()

        for coll_name in collections:
            if "post" in coll_name.lower():
                coll = db[coll_name]
                count = coll.count_documents({})
                print(f"  - {coll_name}: {count} записей")

                for doc in coll.find().limit(1000):  # Лимит для экономии памяти
                    posts.append({"collection": coll_name, "data": doc})

        print(f"✅ Извлечено постов: {len(posts)}")
        return posts
    except Exception as e:
        print(f"❌ Ошибка извлечения постов: {e}")
        return []


def extract_filters(db):
    """Извлечь фильтры для сортировки"""
    print("\n🔍 Извлечение фильтров...")
    filters = []

    try:
        collections = db.list_collection_names()

        for coll_name in collections:
            if "filter" in coll_name.lower() or "rule" in coll_name.lower():
                coll = db[coll_name]
                count = coll.count_documents({})
                print(f"  - {coll_name}: {count} записей")

                for doc in coll.find():
                    filters.append({"collection": coll_name, "data": doc})

        print(f"✅ Извлечено фильтров: {len(filters)}")
        return filters
    except Exception as e:
        print(f"❌ Ошибка извлечения фильтров: {e}")
        return []


def analyze_database(db):
    """Полный анализ базы данных"""
    print("\n🔬 Анализ структуры базы данных...")

    analysis = {"collections": {}, "total_documents": 0, "extracted_at": datetime.now().isoformat()}

    try:
        collections = db.list_collection_names()

        for coll_name in collections:
            coll = db[coll_name]
            count = coll.count_documents({})
            analysis["total_documents"] += count

            # Получить примеры документов
            sample_doc = coll.find_one()

            analysis["collections"][coll_name] = {
                "count": count,
                "sample_keys": list(sample_doc.keys()) if sample_doc else [],
                "sample_doc": str(sample_doc)[:500] if sample_doc else None,
            }

            print(f"  - {coll_name}: {count} документов")

        print(f"\n✅ Всего коллекций: {len(collections)}")
        print(f"✅ Всего документов: {analysis['total_documents']}")

        return analysis
    except Exception as e:
        print(f"❌ Ошибка анализа: {e}")
        return analysis


def save_data(data, filename):
    """Сохранить данные в JSON"""
    filepath = f"/home/valstan/SETKA/old_project_analysis/{filename}"

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


def main():
    print("=" * 60)
    print("🚀 Извлечение данных из старой базы Postopus")
    print("=" * 60)

    client, db = connect_to_mongodb()

    if db is None:
        print("❌ Не удалось подключиться к базе данных")
        return

    try:
        # Анализ структуры
        analysis = analyze_database(db)
        save_data(analysis, "db_analysis.json")

        # Извлечение данных
        communities = extract_communities(db)
        save_data(communities, "communities.json")

        posts = extract_posts(db)
        save_data(posts, "posts_sample.json")

        filters = extract_filters(db)
        save_data(filters, "filters.json")

        print("\n" + "=" * 60)
        print("✅ Извлечение данных завершено!")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
    finally:
        if client:
            client.close()
            print("🔌 Соединение закрыто")


if __name__ == "__main__":
    main()
