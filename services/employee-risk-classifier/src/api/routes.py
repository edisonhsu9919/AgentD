"""
API路由定义
"""

import asyncio
import uuid
import time
import logging
from datetime import datetime
from typing import Dict
from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse
import pandas as pd
import os
from pathlib import Path

from .models import *
from ..core.classifier import UniversalClassifier
from ..core.processor import BatchProcessor
from ..prompts.manager import prompt_manager
from ..prompts.templates import ClassificationLevel
from ..llm.client import LLMClient
from ..llm.models import LLMConfig, LLMProvider, LLMMessage
from ..llm.config_store import LLMConfigStore
from config.settings import settings

# 路由器
router = APIRouter()
logger = logging.getLogger("uvicorn.error")

# 全局变量存储任务状态和分类器实例
processing_tasks: Dict[str, FileProcessingTask] = {}
classifier_instance: UniversalClassifier = None
llm_config_store = LLMConfigStore()

def _config_to_dict(config: LLMConfig) -> Dict:
    return LLMConfigStore.serialize_config(config)

def _build_llm_config(request: LLMConfigRequest) -> LLMConfig:
    provider = str(request.provider).strip()
    api_key = str(request.api_key).strip()
    base_url = str(request.base_url).strip().rstrip("/")
    model = str(request.model).strip()

    return LLMConfig(
        provider=LLMProvider(provider),
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        timeout=request.timeout
    )

def get_classifier() -> UniversalClassifier:
    """获取分类器实例"""
    global classifier_instance
    if classifier_instance is None:
        _, active_config = llm_config_store.get_active_profile()
        classifier_instance = UniversalClassifier(LLMClient(active_config))
    return classifier_instance

@router.get("/", response_model=BaseResponse)
async def root():
    """根路径"""
    return BaseResponse(
        success=True,
        message=f"智能分类器 API v{settings.app_version}"
    )

@router.get("/health", response_model=SystemStatusResponse)
async def health_check():
    """健康检查"""
    try:
        classifier = get_classifier()
        llm_config = classifier.llm_client.get_config_info()
        
        return SystemStatusResponse(
            success=True,
            status={
                "service": "online",
                "version": settings.app_version,
                "llm_provider": llm_config.get("provider"),
                "llm_model": llm_config.get("model"),
                "uptime": "healthy"
            }
        )
    except Exception as e:
        return SystemStatusResponse(
            success=False,
            status={"service": "error"},
            error=str(e)
        )

@router.post("/classification/single", response_model=SingleClassificationResponse)
async def classify_single(request: SingleClassificationRequest):
    """单条分类"""
    try:
        classifier = get_classifier()
        
        # 确定使用的任务类型
        task_type = request.custom_template or request.task_type.value
        
        start_time = time.time()
        result = await classifier.classify_async(
            job_title=request.job_title,
            company_name=request.company_name,
            task_type=task_type
        )
        processing_time = time.time() - start_time
        
        if result["success"]:
            classification_result = ClassificationResult(
                job_title=request.job_title,
                company_name=request.company_name,
                classification=result["classification"],
                reason=result["reason"],
                processing_time=processing_time
            )
            
            return SingleClassificationResponse(
                success=True,
                result=classification_result,
                task_type=task_type,
                template_used=task_type
            )
        else:
            logger.warning(
                "single_classification_failed task=%s job=%s company=%s error=%s",
                task_type,
                request.job_title,
                request.company_name or "",
                result.get("error", "")
            )
            return SingleClassificationResponse(
                success=False,
                error=result["error"],
                task_type=task_type,
                template_used=task_type
            )
            
    except Exception as e:
        return SingleClassificationResponse(
            success=False,
            error=f"分类失败: {str(e)}",
            task_type=request.task_type.value,
            template_used=""
        )

@router.post("/classification/batch", response_model=BatchClassificationResponse)
async def classify_batch(request: BatchClassificationRequest):
    """批量分类"""
    try:
        classifier = get_classifier()
        
        # 确定使用的任务类型
        task_type = request.custom_template or request.task_type.value
        
        start_time = time.time()
        results = []
        errors = []
        
        # 并发处理（限制并发数）
        semaphore = asyncio.Semaphore(5)  # 限制并发数为5
        
        async def process_item(item: SingleClassificationRequest, index: int):
            async with semaphore:
                try:
                    result = await classifier.classify_async(
                        job_title=item.job_title,
                        company_name=item.company_name,
                        task_type=task_type
                    )
                    
                    if result["success"]:
                        return ClassificationResult(
                            job_title=item.job_title,
                            company_name=item.company_name,
                            classification=result["classification"],
                            reason=result["reason"]
                        )
                    else:
                        logger.warning(
                            "batch_classification_item_failed task=%s index=%s job=%s error=%s",
                            task_type,
                            index,
                            item.job_title,
                            result.get("error", "")
                        )
                        errors.append({
                            "index": index,
                            "job_title": item.job_title,
                            "error": result["error"]
                        })
                        return None
                        
                except Exception as e:
                    errors.append({
                        "index": index,
                        "job_title": item.job_title,
                        "error": str(e)
                    })
                    return None
        
        # 并发执行
        tasks = [process_item(item, i) for i, item in enumerate(request.items)]
        completed_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理结果
        for result in completed_results:
            if isinstance(result, ClassificationResult):
                results.append(result)
            elif isinstance(result, Exception):
                errors.append({
                    "error": str(result)
                })
        
        processing_time = time.time() - start_time
        
        return BatchClassificationResponse(
            success=True,
            results=results,
            errors=errors,
            total_count=len(request.items),
            success_count=len(results),
            error_count=len(errors),
            processing_time=processing_time,
            task_type=task_type
        )
        
    except Exception as e:
        return BatchClassificationResponse(
            success=False,
            errors=[{"error": f"批量分类失败: {str(e)}"}],
            total_count=len(request.items),
            success_count=0,
            error_count=len(request.items),
            processing_time=0.0,
            task_type=request.task_type.value
        )

@router.post("/file/upload", response_model=FileProcessingResponse)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    job_column: str = "岗位名称",
    company_column: str = "公司名称",
    task_type: TaskType = TaskType.RISK_ASSESSMENT,
    custom_template: str = None
):
    """文件上传和处理"""
    try:
        # 验证文件类型
        if not file.filename.endswith(('.xlsx', '.xls', '.csv')):
            raise HTTPException(status_code=400, detail="不支持的文件格式")
        
        # 生成任务ID
        task_id = str(uuid.uuid4())
        
        # 保存上传的文件
        upload_path = Path(settings.upload_path)
        file_path = upload_path / f"{task_id}_{file.filename}"
        
        with open(file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)
        
        # 创建处理任务
        task = FileProcessingTask(
            task_id=task_id,
            filename=file.filename,
            status=ProcessingStatus.PENDING
        )
        processing_tasks[task_id] = task
        
        # 启动后台处理
        background_tasks.add_task(
            process_file_background,
            task_id,
            str(file_path),
            job_column,
            company_column,
            custom_template or task_type.value
        )
        
        return FileProcessingResponse(
            success=True,
            task=task
        )
        
    except Exception as e:
        return FileProcessingResponse(
            success=False,
            error=f"文件上传失败: {str(e)}"
        )

async def process_file_background(
    task_id: str,
    file_path: str,
    job_column: str,
    company_column: str,
    task_type: str
):
    """后台文件处理"""
    try:
        task = processing_tasks[task_id]
        task.status = ProcessingStatus.PROCESSING
        
        # 读取文件
        if file_path.endswith('.csv'):
            df = pd.read_csv(file_path)
        else:
            df = pd.read_excel(file_path)
        
        task.total_rows = len(df)
        task.processed_rows = 0
        task.unique_total_rows = 0
        task.processed_unique_rows = 0
        task.error_rows = 0

        # 复用BatchProcessor去重算法（工种+公司组合唯一调用）
        processor = BatchProcessor(get_classifier())

        # 先在后台计算去重后待处理数量，便于前端清晰展示
        if job_column not in df.columns:
            available_cols = list(df.columns)
            matched_job_column = processor._find_similar_column(job_column, available_cols)
            if matched_job_column:
                job_column = matched_job_column
            else:
                raise ValueError(f"找不到岗位列：{job_column}，可用列：{available_cols}")

        if company_column not in df.columns:
            available_cols = list(df.columns)
            matched_company_column = processor._find_similar_column(company_column, available_cols)
            if matched_company_column:
                company_column = matched_company_column
            else:
                df[company_column] = ""

        unique_keys = set()
        for _, row in df.iterrows():
            job_title = processor._normalize_text(row[job_column])
            if not job_title:
                continue
            company_name = processor._normalize_text(row[company_column])
            unique_keys.add((job_title, company_name))

        task.unique_total_rows = len(unique_keys)
        if task.unique_total_rows == 0:
            task.progress = 1.0

        def progress_callback(current: int, total: int, message: str):
            if not task.total_rows:
                return

            if not task.unique_total_rows:
                task.processed_unique_rows = 0
                task.processed_rows = 0
                task.progress = 1.0
                return

            target = task.unique_total_rows
            normalized_current = min(max(current, 0), target)
            ratio = min(max(normalized_current / target, 0.0), 1.0)
            task.processed_unique_rows = normalized_current
            task.processed_rows = normalized_current
            task.progress = ratio

        processor.set_progress_callback(progress_callback)
        result_df = await processor.process_dataframe_async(
            df=df,
            job_column=job_column,
            company_column=company_column,
            task_type=task_type,
            max_concurrent=5
        )

        task.error_rows = len(result_df[result_df["处理状态"].str.contains("失败|错误", na=False)])
        task.processed_unique_rows = task.unique_total_rows
        task.processed_rows = task.unique_total_rows
        task.progress = 1.0
        
        # 保存结果文件
        output_path = Path(settings.output_path)
        result_filename = f"result_{task_id}_{Path(file_path).name}"
        result_path = output_path / result_filename
        
        if file_path.endswith('.csv'):
            result_df.to_csv(result_path, index=False, encoding='utf-8-sig')
        else:
            result_df.to_excel(result_path, index=False)
        
        # 更新任务状态
        task.status = ProcessingStatus.COMPLETED
        task.completed_at = datetime.now()
        task.download_url = f"/file/download/{task_id}"
        
        # 清理上传的文件
        os.remove(file_path)
        
    except Exception as e:
        task = processing_tasks[task_id]
        task.status = ProcessingStatus.FAILED
        task.error_message = str(e)
        task.completed_at = datetime.now()

@router.get("/file/status/{task_id}", response_model=FileProcessingResponse)
async def get_file_status(task_id: str):
    """获取文件处理状态"""
    if task_id not in processing_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    task = processing_tasks[task_id]
    return FileProcessingResponse(
        success=True,
        task=task
    )

@router.get("/file/download/{task_id}")
async def download_file(task_id: str):
    """下载处理结果文件"""
    output_path = Path(settings.output_path)
    
    # 查找匹配的结果文件（支持服务器重启后的下载）
    result_files = list(output_path.glob(f"result_{task_id}_*"))
    
    if not result_files:
        raise HTTPException(status_code=404, detail="结果文件不存在")
    
    result_path = result_files[0]  # 取第一个匹配的文件
    
    # 提取原始文件名
    filename_parts = result_path.name.split('_', 2)
    if len(filename_parts) >= 3:
        original_filename = filename_parts[2]
        download_filename = f"classified_{original_filename}"
    else:
        download_filename = f"classified_{result_path.name}"
    
    return FileResponse(
        path=str(result_path),
        filename=download_filename,
        media_type='application/octet-stream'
    )

@router.get("/templates", response_model=TemplateListResponse)
async def list_templates():
    """获取所有可用模板"""
    try:
        templates_info = prompt_manager.list_templates()
        templates = []
        
        for template_id, description in templates_info.items():
            template_info = prompt_manager.get_template_info(template_id)
            if template_info:
                templates.append(TemplateInfo(
                    id=template_id,
                    name=template_info.get("task_name", template_info.get("task", template_id)),
                    description=description,
                    type=template_info["type"],
                    levels=template_info["levels"]
                ))
        
        return TemplateListResponse(
            success=True,
            templates=templates
        )
        
    except Exception as e:
        return TemplateListResponse(
            success=False,
            error=f"获取模板列表失败: {str(e)}"
        )

@router.get("/templates/{template_id}")
async def get_template_info(template_id: str):
    """获取单个模板详情"""
    try:
        template_info = prompt_manager.get_template_info(template_id)
        if template_info:
            return {
                "success": True,
                "template": template_info
            }
        else:
            return {
                "success": False,
                "error": f"模板 {template_id} 不存在"
            }
    except Exception as e:
        return {
            "success": False,
            "error": f"获取模板信息失败: {str(e)}"
        }

@router.post("/templates/custom", response_model=CustomTemplateResponse)
async def create_custom_template(request: CustomTemplateRequest):
    """创建自定义模板"""
    try:
        # 转换分类等级格式
        levels = []
        for level_data in request.classification_levels:
            level = ClassificationLevel(
                id=level_data["id"],
                name=level_data["name"],
                description=level_data["description"],
                risk_characteristics=level_data.get("risk_characteristics", []),
                typical_jobs=level_data.get("typical_jobs", [])
            )
            levels.append(level)
        
        # 添加自定义模板
        prompt_manager.add_custom_template(
            name=request.name,
            task_name=request.task_name,
            system_prompt=request.system_prompt,
            classification_levels=levels,
            output_format=request.output_format
        )
        
        # 返回创建的模板信息
        template_info = prompt_manager.get_template_info(f"custom_{request.name}")
        
        return CustomTemplateResponse(
            success=True,
            template=TemplateInfo(
                id=f"custom_{request.name}",
                name=request.task_name,
                description=f"自定义模板：{request.task_name}",
                type="custom",
                levels=template_info["levels"]
            )
        )
        
    except Exception as e:
        return CustomTemplateResponse(
            success=False,
            error=f"创建自定义模板失败: {str(e)}"
        )

@router.put("/templates/custom/{name}", response_model=CustomTemplateResponse)
async def update_custom_template(name: str, request: CustomTemplateRequest):
    """更新自定义模板"""
    try:
        # 检查模板是否存在
        if not prompt_manager.get_template_info(f"custom_{name}"):
            return CustomTemplateResponse(
                success=False,
                error=f"模板 {name} 不存在"
            )
        
        # 转换分类等级格式
        levels = []
        for level_data in request.classification_levels:
            level = ClassificationLevel(
                id=level_data["id"],
                name=level_data["name"],
                description=level_data["description"],
                risk_characteristics=level_data.get("risk_characteristics", []),
                typical_jobs=level_data.get("typical_jobs", [])
            )
            levels.append(level)
        
        # 删除旧模板
        prompt_manager.remove_custom_template(name)
        
        # 添加新模板
        prompt_manager.add_custom_template(
            name=request.name,
            task_name=request.task_name,
            system_prompt=request.system_prompt,
            classification_levels=levels,
            output_format=request.output_format
        )
        
        # 返回更新的模板信息
        template_info = prompt_manager.get_template_info(f"custom_{request.name}")
        
        return CustomTemplateResponse(
            success=True,
            template=TemplateInfo(
                id=f"custom_{request.name}",
                name=request.task_name,
                description=f"自定义模板：{request.task_name}",
                type="custom",
                levels=template_info["levels"]
            )
        )
        
    except Exception as e:
        return CustomTemplateResponse(
            success=False,
            error=f"更新自定义模板失败: {str(e)}"
        )

@router.delete("/templates/custom/{name}", response_model=BaseResponse)
async def delete_custom_template(name: str):
    """删除自定义模板"""
    try:
        success = prompt_manager.remove_custom_template(name)
        if success:
            return BaseResponse(
                success=True,
                message=f"模板 {name} 删除成功"
            )
        else:
            return BaseResponse(
                success=False,
                error=f"模板 {name} 不存在"
            )
            
    except Exception as e:
        return BaseResponse(
            success=False,
            error=f"删除模板失败: {str(e)}"
        )

@router.get("/llm/config", response_model=LLMConfigResponse)
async def get_llm_config():
    """获取当前LLM配置"""
    try:
        active_name, active_config = llm_config_store.get_active_profile()
        config = _config_to_dict(active_config)
        config["active_profile"] = active_name

        return LLMConfigResponse(
            success=True,
            config=config
        )
        
    except Exception as e:
        return LLMConfigResponse(
            success=False,
            error=f"获取LLM配置失败: {str(e)}"
        )

@router.post("/llm/config", response_model=LLMConfigResponse)
async def update_llm_config(request: LLMConfigRequest):
    """更新LLM配置"""
    try:
        llm_config = _build_llm_config(request)
        active_name, _ = llm_config_store.get_active_profile()
        profile_name = (request.profile_name or active_name).strip()
        llm_config_store.save_profile(profile_name, llm_config, set_active=request.set_active)

        if request.set_active:
            classifier = get_classifier()
            classifier.llm_client = LLMClient(llm_config)

        current_active_name, current_active_config = llm_config_store.get_active_profile()
        config_dict = _config_to_dict(current_active_config)
        config_dict["active_profile"] = current_active_name

        return LLMConfigResponse(
            success=True,
            config=config_dict
        )
        
    except Exception as e:
        return LLMConfigResponse(
            success=False,
            error=f"更新LLM配置失败: {str(e)}"
        )

@router.get("/llm/config/profiles", response_model=LLMProfileListResponse)
async def list_llm_profiles():
    """获取本地存储的LLM配置档案"""
    try:
        active_name, _ = llm_config_store.get_active_profile()
        profiles = llm_config_store.list_profiles()
        profile_items = [
            LLMProfileInfo(
                name=name,
                config=_config_to_dict(config),
                is_active=(name == active_name)
            )
            for name, config in profiles.items()
        ]
        profile_items.sort(key=lambda item: (not item.is_active, item.name))

        return LLMProfileListResponse(
            success=True,
            profiles=profile_items,
            active_profile=active_name
        )
    except Exception as e:
        return LLMProfileListResponse(
            success=False,
            error=f"获取LLM配置档案失败: {str(e)}"
        )

@router.post("/llm/config/profiles", response_model=LLMConfigResponse)
async def save_llm_profile(request: LLMProfileRequest):
    """保存LLM配置档案"""
    try:
        llm_config = _build_llm_config(request.config)
        llm_config_store.save_profile(request.name, llm_config, set_active=request.set_active)

        if request.set_active:
            classifier = get_classifier()
            classifier.llm_client = LLMClient(llm_config)

        active_name, active_config = llm_config_store.get_active_profile()
        config_dict = _config_to_dict(active_config)
        config_dict["active_profile"] = active_name

        return LLMConfigResponse(
            success=True,
            config=config_dict
        )
    except Exception as e:
        return LLMConfigResponse(
            success=False,
            error=f"保存LLM配置档案失败: {str(e)}"
        )

@router.post("/llm/config/profiles/{name}/activate", response_model=LLMConfigResponse)
async def activate_llm_profile(name: str):
    """激活指定LLM配置档案"""
    try:
        llm_config = llm_config_store.set_active_profile(name)
        classifier = get_classifier()
        classifier.llm_client = LLMClient(llm_config)

        config_dict = _config_to_dict(llm_config)
        config_dict["active_profile"] = name

        return LLMConfigResponse(
            success=True,
            config=config_dict
        )
    except Exception as e:
        return LLMConfigResponse(
            success=False,
            error=f"激活LLM配置档案失败: {str(e)}"
        )

@router.delete("/llm/config/profiles/{name}", response_model=BaseResponse)
async def delete_llm_profile(name: str):
    """删除指定LLM配置档案"""
    try:
        deleted = llm_config_store.delete_profile(name)
        if not deleted:
            return BaseResponse(
                success=False,
                error=f"配置档案 {name} 不存在"
            )

        active_name, active_config = llm_config_store.get_active_profile()
        classifier = get_classifier()
        classifier.llm_client = LLMClient(active_config)

        return BaseResponse(
            success=True,
            message=f"配置档案 {name} 已删除，当前激活配置为 {active_name}"
        )
    except Exception as e:
        return BaseResponse(
            success=False,
            error=f"删除LLM配置档案失败: {str(e)}"
        )

@router.post("/llm/test", response_model=LLMTestResponse)
async def test_llm_connection(request: LLMTestRequest):
    """真实测试LLM调用连通性"""
    try:
        if request.config:
            llm_config = _build_llm_config(request.config)
            profile_name = request.config.profile_name or "adhoc"
        else:
            profile_name, llm_config = llm_config_store.get_active_profile()

        llm_client = LLMClient(llm_config)
        response = await llm_client.generate([
            LLMMessage(role="system", content="你是一个测试助手。"),
            LLMMessage(role="user", content=request.message)
        ])

        log_message = (
            "llm_test profile=%s provider=%s base_url=%s model=%s success=%s error=%s"
        )
        log_args = (
            profile_name,
            llm_config.provider.value,
            llm_config.base_url,
            llm_config.model,
            response.is_success,
            response.error or ""
        )
        if response.is_success:
            logger.info(log_message, *log_args)
        else:
            logger.warning(log_message, *log_args)

        return LLMTestResponse(
            success=True,
            test_success=response.is_success,
            provider=llm_config.provider.value,
            model=llm_config.model,
            response=response.content if response.is_success else None,
            error=response.error if not response.is_success else None
        )

    except Exception as e:
        logger.exception("llm_test_exception")
        return LLMTestResponse(
            success=False,
            test_success=False,
            error=f"LLM测试失败: {str(e)}"
        )
