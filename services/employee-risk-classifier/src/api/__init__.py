"""
API模块 - FastAPI后端接口
"""

from .app import create_app
from .models import *
from .routes import *

__all__ = ["create_app"]