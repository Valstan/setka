#!/usr/bin/env python3
"""
Демонстрация всех категорий групп регионов

Этот скрипт показывает все категории групп для каждого региона
с новыми тематиками: развлекательные и новости науки.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.region_config import REGIONS_CONFIG, CommunityCategory, RegionConfigManager


def show_all_categories():
    """Показать все категории групп для всех регионов"""
    print("🎯 ПОЛНАЯ КАТЕГОРИЗАЦИЯ ГРУПП РЕГИОНОВ")
    print("=" * 80)

    # Словарь для перевода категорий на русский
    category_names = {
        CommunityCategory.ADMINISTRATION: "Администрация",
        CommunityCategory.CULTURE: "Культура",
        CommunityCategory.YOUTH: "Молодежь",
        CommunityCategory.SPORTS: "Спорт",
        CommunityCategory.PRESCHOOL_EDUCATION: "Дошкольное образование",
        CommunityCategory.NEWS: "Новости",
        CommunityCategory.ORTHODOX_NEWS: "Православные новости",
        CommunityCategory.ADVERTISING: "Реклама",
        CommunityCategory.ENTERTAINMENT: "Развлекательные",
        CommunityCategory.SCIENCE_NEWS: "Новости науки",
    }

    for region_code, config in REGIONS_CONFIG.items():
        print(f"\n📍 {region_code.upper()}: {config.name}")
        print("-" * 60)

        for category in CommunityCategory:
            groups = RegionConfigManager.get_community_groups_by_category(region_code, category)
            if groups:
                category_name = category_names[category]
                print(f"  {category_name}: {groups}")


def show_category_summary():
    """Показать сводку по категориям"""
    print("\n📊 СВОДКА ПО КАТЕГОРИЯМ")
    print("=" * 50)

    category_counts = {}

    for category in CommunityCategory:
        count = 0
        for region_code in REGIONS_CONFIG.keys():
            groups = RegionConfigManager.get_community_groups_by_category(region_code, category)
            count += len(groups)
        category_counts[category] = count

    # Сортируем по количеству групп
    sorted_categories = sorted(category_counts.items(), key=lambda x: x[1], reverse=True)

    for category, count in sorted_categories:
        print(f"  {category.value}: {count} групп")


def show_region_summary():
    """Показать сводку по регионам"""
    print("\n🌍 СВОДКА ПО РЕГИОНАМ")
    print("=" * 50)

    for region_code, config in REGIONS_CONFIG.items():
        total_groups = len(RegionConfigManager.get_all_community_groups(region_code))
        print(f"  {region_code.upper()}: {total_groups} групп")


def main():
    """Главная функция"""
    import argparse

    parser = argparse.ArgumentParser(description="Демонстрация категорий групп регионов")
    parser.add_argument("--all", action="store_true", help="Показать все категории")
    parser.add_argument("--summary", action="store_true", help="Показать сводку по категориям")
    parser.add_argument("--regions", action="store_true", help="Показать сводку по регионам")

    args = parser.parse_args()

    if args.all:
        show_all_categories()
    elif args.summary:
        show_category_summary()
    elif args.regions:
        show_region_summary()
    else:
        # По умолчанию показываем все
        show_all_categories()
        show_category_summary()
        show_region_summary()


if __name__ == "__main__":
    main()
