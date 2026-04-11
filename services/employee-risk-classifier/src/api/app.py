"""
FastAPI应用程序
"""

import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pathlib import Path
import logging

from .routes import router
from config.settings import settings

def create_app() -> FastAPI:
    """创建FastAPI应用"""
    
    # 创建FastAPI实例
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="智能职业分类系统 - 支持多种分类任务的AI驱动的职业分析工具",
        docs_url="/docs",
        redoc_url="/redoc"
    )
    
    # 配置CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 生产环境应该限制具体域名
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # 添加静态文件服务（如果有前端文件）
    frontend_path = Path("frontend/dist")
    if frontend_path.exists():
        app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")
    
    # 注册路由
    app.include_router(router, prefix="/api")
    
    # 全局异常处理
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """全局异常处理器"""
        logging.error(f"Global exception: {str(exc)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "服务器内部错误",
                "detail": str(exc) if settings.debug else "内部服务器错误"
            }
        )
    
    # 启动事件
    @app.on_event("startup")
    async def startup_event():
        """应用启动事件"""
        logging.info(f"启动 {settings.app_name} v{settings.app_version}")
        
        # 确保必要的目录存在
        Path(settings.upload_path).mkdir(parents=True, exist_ok=True)
        Path(settings.output_path).mkdir(parents=True, exist_ok=True)
        
        logging.info("应用启动完成")
    
    # 关闭事件
    @app.on_event("shutdown")
    async def shutdown_event():
        """应用关闭事件"""
        logging.info("应用正在关闭...")
        # 清理资源
        logging.info("应用关闭完成")
    
    return app

# 创建应用实例
app = create_app()

if __name__ == "__main__":
    import uvicorn
    
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    # 启动服务器
    uvicorn.run(
        "src.api.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="info"
    )