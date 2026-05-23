"""
Test VK Client Performance: Sync vs Async
Сравнение производительности старого синхронного и нового асинхронного VK клиента
"""

import asyncio
import sys
import time
from statistics import mean

# Add project root to path
sys.path.insert(0, "/home/valstan/SETKA")

from config.runtime import VK_TOKENS  # noqa: E402
from modules.vk_monitor.vk_client import VKClient  # noqa: E402
from modules.vk_monitor.vk_client_async import VKClientAsync  # noqa: E402


async def test_sync_client(token: str, test_communities: list, iterations: int = 3):
    """Test synchronous VK client"""
    print("\n" + "=" * 60)
    print("🔵 ТЕСТ СИНХРОННОГО КЛИЕНТА (vk_api)")
    print("=" * 60)

    client = VKClient(token)
    times = []

    for i in range(iterations):
        start = time.time()

        total_posts = 0
        for community_id in test_communities:
            posts = client.get_wall_posts(owner_id=community_id, count=10)
            total_posts += len(posts)
            await asyncio.sleep(0.5)  # Avoid rate limit

        elapsed = time.time() - start
        times.append(elapsed)

        print(f"\nИтерация {i+1}/{iterations}:")
        print(f"  Время: {elapsed:.2f}s")
        print(f"  Постов получено: {total_posts}")

    avg_time = mean(times)
    print("\n📊 Средний результат:")
    print(f"  Среднее время: {avg_time:.2f}s")
    print(f"  Запросов в секунду: {len(test_communities)/avg_time:.2f}")

    return avg_time


async def test_async_client(token: str, test_communities: list, iterations: int = 3):
    """Test asynchronous VK client with connection pooling"""
    print("\n" + "=" * 60)
    print("🟢 ТЕСТ АСИНХРОННОГО КЛИЕНТА (aiohttp + pooling)")
    print("=" * 60)

    times = []

    for i in range(iterations):
        async with VKClientAsync(token) as client:
            start = time.time()

            # Выполняем запросы параллельно!
            tasks = [
                client.get_wall_posts(owner_id=community_id, count=10)
                for community_id in test_communities
            ]

            results = await asyncio.gather(*tasks)
            total_posts = sum(len(posts) for posts in results)

            elapsed = time.time() - start
            times.append(elapsed)

            print(f"\nИтерация {i+1}/{iterations}:")
            print(f"  Время: {elapsed:.2f}s")
            print(f"  Постов получено: {total_posts}")

    avg_time = mean(times)
    print("\n📊 Средний результат:")
    print(f"  Среднее время: {avg_time:.2f}s")
    print(f"  Запросов в секунду: {len(test_communities)/avg_time:.2f}")

    return avg_time


async def main():
    """Run performance comparison"""
    print("=" * 60)
    print("🧪 VK CLIENT PERFORMANCE TEST")
    print("=" * 60)

    # Get token
    token = VK_TOKENS.get("VALSTAN")
    if not token:
        print("❌ VK token not found in config!")
        return

    print("\n✅ Token loaded")

    # Test communities (малмыжские группы)
    test_communities = [
        -221432488,  # Малмыж - Инфо
        -30310681,  # Типичный Малмыж
        -48404224,  # Объявления Малмыж
    ]

    print(f"✅ Testing with {len(test_communities)} communities")
    print("✅ Each test: 10 posts per community")
    print("✅ Total iterations: 3")

    # Run tests
    sync_time = await test_sync_client(token, test_communities, iterations=3)
    await asyncio.sleep(2)  # Pause between tests
    async_time = await test_async_client(token, test_communities, iterations=3)

    # Compare results
    print("\n" + "=" * 60)
    print("📈 СРАВНЕНИЕ РЕЗУЛЬТАТОВ")
    print("=" * 60)

    improvement = ((sync_time - async_time) / sync_time) * 100
    speedup = sync_time / async_time

    print("\n🔵 Синхронный клиент:")
    print(f"   Среднее время: {sync_time:.2f}s")
    print(f"   RPS: {len(test_communities)/sync_time:.2f}")

    print("\n🟢 Асинхронный клиент:")
    print(f"   Среднее время: {async_time:.2f}s")
    print(f"   RPS: {len(test_communities)/async_time:.2f}")

    print("\n🏆 РЕЗУЛЬТАТ:")
    print(f"   Улучшение: {improvement:.1f}%")
    print(f"   Ускорение: {speedup:.1f}x faster")

    if speedup > 2:
        print("   ✅ ОТЛИЧНО! Значительное ускорение!")
    elif speedup > 1.5:
        print("   ✅ ХОРОШО! Заметное улучшение!")
    else:
        print("   ⚠️  Умеренное улучшение")

    print("\n" + "=" * 60)
    print("Преимущества асинхронного клиента:")
    print("  ✅ Connection pooling - переиспользование соединений")
    print("  ✅ Async requests - параллельные запросы")
    print("  ✅ Automatic retries - авто-повторы при ошибках")
    print("  ✅ Better error handling")
    print("=" * 60)

    print("\n✅ Тест завершён!")


if __name__ == "__main__":
    print("\n⚠️  Тест выполнит несколько запросов к VK API")
    print("⚠️  Убедитесь, что токен валиден\n")

    asyncio.run(main())
