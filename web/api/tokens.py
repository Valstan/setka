"""
Token Management API - Web interface for VK token management
"""
from fastapi import APIRouter, HTTPException, Form
from typing import Dict, List, Optional
from pydantic import BaseModel
import logging

from modules.token_manager import token_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


class TokenAddRequest(BaseModel):
    name: str
    token: str
    token_type: str = "MAIN"  # MAIN or AUXILIARY


class TokenResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict] = None


@router.get("/", response_model=Dict)
async def get_all_tokens():
    """Get information about all tokens"""
    try:
        return {
            "success": True,
            "data": token_manager.get_all_tokens_info()
        }
    except Exception as e:
        logger.error(f"Error getting tokens: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/check", response_model=Dict)
async def check_all_tokens():
    """Check status of all tokens"""
    try:
        results = token_manager.check_all_tokens()
        return {
            "success": True,
            "data": results
        }
    except Exception as e:
        logger.error(f"Error checking tokens: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/add", response_model=TokenResponse)
async def add_token(request: TokenAddRequest):
    """Add new VK token"""
    try:
        if request.token_type not in ["MAIN", "AUXILIARY"]:
            raise HTTPException(status_code=400, detail="token_type must be 'MAIN' or 'AUXILIARY'")
        
        success, message = token_manager.add_token(
            request.name, 
            request.token, 
            request.token_type
        )
        
        return TokenResponse(
            success=success,
            message=message,
            data={"name": request.name, "type": request.token_type} if success else None
        )
        
    except Exception as e:
        logger.error(f"Error adding token: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{token_name}", response_model=TokenResponse)
async def remove_token(token_name: str):
    """Remove token by name"""
    try:
        success, message = token_manager.remove_token(token_name)
        
        return TokenResponse(
            success=success,
            message=message,
            data={"name": token_name} if success else None
        )
        
    except Exception as e:
        logger.error(f"Error removing token: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{token_name}", response_model=Dict)
async def get_token_info(token_name: str):
    """Get detailed information about specific token"""
    try:
        info = token_manager.get_token_info(token_name)
        if info is None:
            raise HTTPException(status_code=404, detail=f"Token '{token_name}' not found")
        
        return {
            "success": True,
            "data": info
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting token info: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/for/{operation}", response_model=Dict)
async def get_tokens_for_operation(operation: str):
    """Get tokens suitable for specific operation (read, post, admin)"""
    try:
        if operation not in ["read", "post", "admin"]:
            raise HTTPException(status_code=400, detail="Operation must be 'read', 'post', or 'admin'")
        
        tokens = token_manager.get_tokens_for_operation(operation)
        
        return {
            "success": True,
            "data": {
                "operation": operation,
                "tokens": tokens,
                "count": len(tokens)
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting tokens for operation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/validate", response_model=Dict)
async def validate_token(token: str = Form(...)):
    """Validate VK token without adding it"""
    try:
        is_valid, message, user_info = token_manager.validate_token(token)
        
        return {
            "success": True,
            "data": {
                "is_valid": is_valid,
                "message": message,
                "user_info": user_info if is_valid else None
            }
        }
        
    except Exception as e:
        logger.error(f"Error validating token: {e}")
        raise HTTPException(status_code=500, detail=str(e))
