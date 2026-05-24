#!/usr/bin/env python3
"""Trigger parse_and_publish_theme via Celery"""

from tasks.parsing_scheduler_tasks import parse_and_publish_theme

print("Triggering parse_and_publish_theme('test', 'novost', test_mode=False)...")
result = parse_and_publish_theme.delay("test", "novost", test_mode=False)
print(f"Task ID: {result.id}")
print("Waiting for result...")

# Wait up to 120 seconds
try:
    output = result.get(timeout=120)
    print(f"\n✅ Result: {output}")
except Exception as e:
    print(f"\n❌ Error: {e}")
    # Check partial result
    if result.ready():
        print(f"Result: {result.result}")
    elif result.failed():
        print(f"Failed: {result.traceback}")
