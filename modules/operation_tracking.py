"""
Operation Tracking Module - Track current system operations for monitoring
"""
import asyncio
from datetime import datetime
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class OperationTracker:
    """Track current system operations"""
    
    def __init__(self):
        self.operations: Dict[str, Dict] = {}
        self.max_operations = 50
    
    def start_operation(self, operation_id: str, operation_type: str, 
                       description: str, region: Optional[str] = None, 
                       details: Optional[Dict] = None):
        """Start tracking an operation"""
        self.operations[operation_id] = {
            "id": operation_id,
            "type": operation_type,
            "description": description,
            "region": region,
            "status": "active",
            "start_time": datetime.utcnow(),
            "end_time": None,
            "details": details or {}
        }
        
        # Keep only recent operations
        if len(self.operations) > self.max_operations:
            oldest_id = min(self.operations.keys(), 
                          key=lambda k: self.operations[k]["start_time"])
            del self.operations[oldest_id]
        
        logger.info(f"Started operation {operation_id}: {description}")
    
    def update_operation(self, operation_id: str, **kwargs):
        """Update operation details"""
        if operation_id in self.operations:
            self.operations[operation_id].update(kwargs)
            logger.debug(f"Updated operation {operation_id}")
    
    def end_operation(self, operation_id: str, status: str = "completed", 
                     details: Optional[Dict] = None):
        """End an operation"""
        if operation_id in self.operations:
            self.operations[operation_id].update({
                "status": status,
                "end_time": datetime.utcnow(),
                "details": {**self.operations[operation_id].get("details", {}), 
                          **(details or {})}
            })
            logger.info(f"Ended operation {operation_id}: {status}")
    
    def get_active_operations(self) -> List[Dict]:
        """Get all active operations"""
        return [
            op for op in self.operations.values() 
            if op["status"] == "active"
        ]
    
    def get_recent_operations(self, limit: int = 10) -> List[Dict]:
        """Get recent operations"""
        sorted_ops = sorted(
            self.operations.values(),
            key=lambda x: x["start_time"],
            reverse=True
        )
        return sorted_ops[:limit]
    
    def get_operation(self, operation_id: str) -> Optional[Dict]:
        """Get specific operation"""
        return self.operations.get(operation_id)
    
    def clear_operations(self):
        """Clear all operations"""
        self.operations.clear()
        logger.info("Cleared all operations")


# Global operation tracker instance
operation_tracker = OperationTracker()


# Context manager for operations
class OperationContext:
    """Context manager for tracking operations"""
    
    def __init__(self, operation_id: str, operation_type: str, 
                 description: str, region: Optional[str] = None,
                 details: Optional[Dict] = None):
        self.operation_id = operation_id
        self.operation_type = operation_type
        self.description = description
        self.region = region
        self.details = details
    
    def __enter__(self):
        operation_tracker.start_operation(
            self.operation_id,
            self.operation_type,
            self.description,
            self.region,
            self.details
        )
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            operation_tracker.end_operation(
                self.operation_id,
                status="error",
                details={"error": str(exc_val)}
            )
        else:
            operation_tracker.end_operation(self.operation_id)
    
    def update(self, **kwargs):
        """Update operation details"""
        operation_tracker.update_operation(self.operation_id, **kwargs)


# Decorator for tracking operations
def track_operation(operation_type: str, description: str, 
                   region: Optional[str] = None):
    """Decorator to track function execution as operation"""
    def decorator(func):
        async def async_wrapper(*args, **kwargs):
            operation_id = f"{func.__name__}_{datetime.utcnow().timestamp()}"
            
            with OperationContext(operation_id, operation_type, description, region):
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                else:
                    return func(*args, **kwargs)
        
        def sync_wrapper(*args, **kwargs):
            operation_id = f"{func.__name__}_{datetime.utcnow().timestamp()}"
            
            with OperationContext(operation_id, operation_type, description, region):
                return func(*args, **kwargs)
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


# Helper functions
def start_monitoring_operation(region_code: str, communities_count: int):
    """Start monitoring operation"""
    operation_id = f"monitoring_{region_code}_{datetime.utcnow().timestamp()}"
    operation_tracker.start_operation(
        operation_id,
        "monitoring",
        f"Мониторинг региона {region_code}",
        region_code,
        {"communities_count": communities_count}
    )
    return operation_id


def start_filtering_operation(region_code: str, posts_count: int):
    """Start filtering operation"""
    operation_id = f"filtering_{region_code}_{datetime.utcnow().timestamp()}"
    operation_tracker.start_operation(
        operation_id,
        "filtering",
        f"Фильтрация постов региона {region_code}",
        region_code,
        {"posts_count": posts_count}
    )
    return operation_id


def start_publishing_operation(region_code: str, posts_count: int):
    """Start publishing operation"""
    operation_id = f"publishing_{region_code}_{datetime.utcnow().timestamp()}"
    operation_tracker.start_operation(
        operation_id,
        "publishing",
        f"Публикация постов региона {region_code}",
        region_code,
        {"posts_count": posts_count}
    )
    return operation_id


def update_operation_progress(operation_id: str, progress: int, 
                             current_step: str, details: Optional[Dict] = None):
    """Update operation progress"""
    operation_tracker.update_operation(
        operation_id,
        progress=progress,
        current_step=current_step,
        details={**operation_tracker.get_operation(operation_id).get("details", {}), 
                **(details or {})}
    )


def end_operation_success(operation_id: str, results: Optional[Dict] = None):
    """End operation with success"""
    operation_tracker.end_operation(
        operation_id,
        status="completed",
        details=results
    )


def end_operation_error(operation_id: str, error: str):
    """End operation with error"""
    operation_tracker.end_operation(
        operation_id,
        status="error",
        details={"error": error}
    )
