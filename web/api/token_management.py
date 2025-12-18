"""
VK Tokens Management API
API для управления токенами VK через веб-интерфейс
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from typing import List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
import logging

from database.connection import get_db_session
from database.models import VKToken
from modules.vk_monitor.vk_client import VKClient

router = APIRouter()
logger = logging.getLogger(__name__)


class TokenResponse(BaseModel):
    """Ответ с информацией о токене"""
    id: int
    name: str
    token: str  # Маскированный токен
    is_active: bool
    last_used: str | None
    last_validated: str | None
    validation_status: str
    error_message: str | None
    permissions: List[str] | None  # Изменено на список
    user_info: Dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


class TokenUpdateRequest(BaseModel):
    """Запрос на обновление токена"""
    token: str = Field(..., min_length=10, description="VK API токен")
    validate_token: bool = Field(True, description="Валидировать токен после обновления")


class TokenValidationResponse(BaseModel):
    """Ответ валидации токена"""
    name: str
    is_valid: bool
    validation_status: str
    error_message: str | None
    user_info: Dict[str, Any] | None
    permissions: List[str] | None


@router.get("/", response_model=List[TokenResponse])
async def get_all_tokens(db: AsyncSession = Depends(get_db_session)):
    """Получить все токены"""
    try:
        result = await db.execute(select(VKToken).order_by(VKToken.name))
        tokens = result.scalars().all()
        
        return [TokenResponse(**token.to_dict()) for token in tokens]
        
    except Exception as e:
        logger.error(f"Error getting tokens: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{token_name}", response_model=TokenResponse)
async def get_token(token_name: str, db: AsyncSession = Depends(get_db_session)):
    """Получить конкретный токен"""
    try:
        result = await db.execute(
            select(VKToken).where(VKToken.name == token_name.upper())
        )
        token = result.scalar_one_or_none()
        
        if not token:
            raise HTTPException(status_code=404, detail=f"Token {token_name} not found")
        
        return TokenResponse(**token.to_dict())
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting token {token_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{token_name}", response_model=TokenResponse)
async def update_token(
    token_name: str, 
    request: TokenUpdateRequest,
    db: AsyncSession = Depends(get_db_session)
):
    """Обновить токен"""
    try:
        # Найти токен
        result = await db.execute(
            select(VKToken).where(VKToken.name == token_name.upper())
        )
        token = result.scalar_one_or_none()
        
        if not token:
            raise HTTPException(status_code=404, detail=f"Token {token_name} not found")
        
        # Обновить токен
        token.token = request.token
        token.validation_status = 'unknown'
        token.error_message = None
        
        # Валидировать токен если требуется
        if request.validate_token:
            validation_result = await validate_single_token(token.token)
            token.validation_status = 'valid' if validation_result['is_valid'] else 'invalid'
            token.error_message = validation_result.get('error_message')
            token.user_info = validation_result.get('user_info')
            token.permissions = validation_result.get('permissions')
            token.last_validated = datetime.now()
        
        await db.commit()
        await db.refresh(token)
        
        logger.info(f"Token {token_name} updated successfully")
        return TokenResponse(**token.to_dict())
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating token {token_name}: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{token_name}/validate", response_model=TokenValidationResponse)
async def validate_token(token_name: str, db: AsyncSession = Depends(get_db_session)):
    """Валидировать токен"""
    try:
        # Найти токен
        result = await db.execute(
            select(VKToken).where(VKToken.name == token_name.upper())
        )
        token = result.scalar_one_or_none()
        
        if not token:
            raise HTTPException(status_code=404, detail=f"Token {token_name} not found")
        
        if not token.token:
            return TokenValidationResponse(
                name=token.name,
                is_valid=False,
                validation_status='invalid',
                error_message="Token is empty",
                user_info=None,
                permissions=None
            )
        
        # Валидировать токен
        validation_result = await validate_single_token(token.token)
        
        # Обновить статус в БД
        token.validation_status = 'valid' if validation_result['is_valid'] else 'invalid'
        token.error_message = validation_result.get('error_message')
        token.user_info = validation_result.get('user_info')
        token.permissions = validation_result.get('permissions')
        token.last_validated = datetime.now()
        
        await db.commit()
        
        return TokenValidationResponse(
            name=token.name,
            is_valid=validation_result['is_valid'],
            validation_status=token.validation_status,
            error_message=validation_result.get('error_message'),
            user_info=validation_result.get('user_info'),
            permissions=validation_result.get('permissions')
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error validating token {token_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/validate-all", response_model=List[TokenValidationResponse])
async def validate_all_tokens(db: AsyncSession = Depends(get_db_session)):
    """Валидировать все токены"""
    try:
        result = await db.execute(select(VKToken))
        tokens = result.scalars().all()
        
        validation_results = []
        
        for token in tokens:
            if not token.token:
                validation_results.append(TokenValidationResponse(
                    name=token.name,
                    is_valid=False,
                    validation_status='invalid',
                    error_message="Token is empty",
                    user_info=None,
                    permissions=None
                ))
                continue
            
            # Валидировать токен
            validation_result = await validate_single_token(token.token)
            
            # Обновить статус в БД
            token.validation_status = 'valid' if validation_result['is_valid'] else 'invalid'
            token.error_message = validation_result.get('error_message')
            token.user_info = validation_result.get('user_info')
            token.permissions = validation_result.get('permissions')
            token.last_validated = datetime.now()
            
            validation_results.append(TokenValidationResponse(
                name=token.name,
                is_valid=validation_result['is_valid'],
                validation_status=token.validation_status,
                error_message=validation_result.get('error_message'),
                user_info=validation_result.get('user_info'),
                permissions=validation_result.get('permissions')
            ))
        
        await db.commit()
        
        logger.info(f"Validated {len(tokens)} tokens")
        return validation_results
        
    except Exception as e:
        logger.error(f"Error validating all tokens: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


async def validate_single_token(token: str) -> Dict[str, Any]:
    """Валидировать один токен"""
    try:
        vk_client = VKClient(token)
        user_info = await vk_client.get_user_info()
        
        if not user_info:
            return {
                'is_valid': False,
                'error_message': 'Failed to get user info',
                'user_info': None,
                'permissions': None
            }
        
        # Получить права доступа
        permissions = []
        try:
            # Тест wall.get
            await vk_client.get_posts(owner_id=-1, count=1)
            permissions.append('wall.read')
        except Exception as e:
            logger.debug(f"wall.read permission test failed: {e}")
        
        try:
            # Тест groups.get
            await vk_client.get_groups(count=1)
            permissions.append('groups.read')
        except Exception as e:
            logger.debug(f"groups.read permission test failed: {e}")
        
        try:
            # Тест messages.get
            await vk_client.get_messages(count=1)
            permissions.append('messages.read')
        except Exception as e:
            logger.debug(f"messages.read permission test failed: {e}")
        
        return {
            'is_valid': True,
            'error_message': None,
            'user_info': user_info,
            'permissions': permissions
        }
        
    except Exception as e:
        return {
            'is_valid': False,
            'error_message': str(e),
            'user_info': None,
            'permissions': None
        }
