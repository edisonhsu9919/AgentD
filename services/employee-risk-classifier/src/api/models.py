"""
API数据模型
"""

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Dict, Any
from enum import Enum
from datetime import datetime

class TaskType(str, Enum):
    """分类任务类型"""
    RISK_ASSESSMENT = "risk_assessment"
    INDUSTRY_CLASSIFICATION = "industry_classification"
    SKILL_ANALYSIS = "skill_analysis"
    CUSTOM = "custom"

class FileFormat(str, Enum):
    """文件格式"""
    XLSX = "xlsx"
    XLS = "xls"
    CSV = "csv"

class ProcessingStatus(str, Enum):
    """处理状态"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

# 请求模型
class SingleClassificationRequest(BaseModel):
    """单条分类请求"""
    job_title: str = Field(..., description="岗位名称", min_length=1)
    company_name: Optional[str] = Field(default="", description="公司名称")
    task_type: TaskType = Field(default=TaskType.RISK_ASSESSMENT, description="分类任务类型")
    custom_template: Optional[str] = Field(default=None, description="自定义模板名称")

class BatchClassificationRequest(BaseModel):
    """批量分类请求"""
    items: List[SingleClassificationRequest] = Field(..., description="分类项目列表", min_items=1)
    task_type: TaskType = Field(default=TaskType.RISK_ASSESSMENT, description="分类任务类型")
    custom_template: Optional[str] = Field(default=None, description="自定义模板名称")

class FileUploadRequest(BaseModel):
    """文件上传请求"""
    file_format: FileFormat = Field(..., description="文件格式")
    job_column: str = Field(default="岗位名称", description="岗位名称列名")
    company_column: str = Field(default="公司名称", description="公司名称列名")
    task_type: TaskType = Field(default=TaskType.RISK_ASSESSMENT, description="分类任务类型")
    custom_template: Optional[str] = Field(default=None, description="自定义模板名称")

class LLMConfigRequest(BaseModel):
    """LLM配置请求"""
    provider: str = Field(..., description="LLM提供商")
    api_key: str = Field(..., description="API密钥")
    base_url: str = Field(..., description="基础URL")
    model: str = Field(..., description="模型名称")
    temperature: float = Field(default=0.1, ge=0.0, le=2.0, description="温度参数")
    max_tokens: int = Field(default=512, gt=0, description="最大令牌数")
    timeout: int = Field(default=60, gt=0, description="超时时间（秒）")
    profile_name: Optional[str] = Field(default=None, description="配置档案名称")
    set_active: bool = Field(default=True, description="是否设为当前激活配置")

class LLMProfileRequest(BaseModel):
    """保存LLM配置档案请求"""
    name: str = Field(..., description="配置档案名称", min_length=1)
    config: LLMConfigRequest = Field(..., description="LLM配置")
    set_active: bool = Field(default=False, description="保存后是否激活")

class LLMTestRequest(BaseModel):
    """LLM连接测试请求"""
    config: Optional[LLMConfigRequest] = Field(default=None, description="可选：使用指定配置测试，不传则用当前激活配置")
    message: str = Field(default="请回复：测试成功", description="测试消息")

class CustomTemplateRequest(BaseModel):
    """自定义模板请求"""
    name: str = Field(..., description="模板名称", min_length=1)
    task_name: str = Field(..., description="任务名称", min_length=1)
    system_prompt: str = Field(..., description="系统提示词", min_length=10)
    classification_levels: List[Dict[str, Any]] = Field(..., description="分类等级定义", min_items=2)
    output_format: str = Field(default="分类结果：{classification}\n理由：{reason}", description="输出格式")

# 响应模型
class ClassificationResult(BaseModel):
    """分类结果"""
    job_title: str = Field(..., description="岗位名称")
    company_name: str = Field(..., description="公司名称")
    classification: str = Field(..., description="分类结果")
    reason: str = Field(..., description="分类理由")
    confidence: Optional[float] = Field(default=None, description="置信度")
    processing_time: Optional[float] = Field(default=None, description="处理耗时（秒）")

class SingleClassificationResponse(BaseModel):
    """单条分类响应"""
    success: bool = Field(..., description="是否成功")
    result: Optional[ClassificationResult] = Field(default=None, description="分类结果")
    error: Optional[str] = Field(default=None, description="错误信息")
    task_type: str = Field(..., description="任务类型")
    template_used: str = Field(..., description="使用的模板")

class BatchClassificationResponse(BaseModel):
    """批量分类响应"""
    success: bool = Field(..., description="是否成功")
    results: List[ClassificationResult] = Field(default=[], description="分类结果列表")
    errors: List[Dict[str, Any]] = Field(default=[], description="错误列表")
    total_count: int = Field(..., description="总数量")
    success_count: int = Field(..., description="成功数量")
    error_count: int = Field(..., description="错误数量")
    processing_time: float = Field(..., description="总处理耗时（秒）")
    task_type: str = Field(..., description="任务类型")

class FileProcessingTask(BaseModel):
    """文件处理任务"""
    task_id: str = Field(..., description="任务ID")
    filename: str = Field(..., description="文件名")
    status: ProcessingStatus = Field(..., description="处理状态")
    progress: float = Field(default=0.0, ge=0.0, le=1.0, description="处理进度")
    total_rows: Optional[int] = Field(default=None, description="总行数")
    processed_rows: Optional[int] = Field(default=None, description="已处理行数")
    unique_total_rows: Optional[int] = Field(default=None, description="去重后待处理总数（岗位+公司）")
    processed_unique_rows: Optional[int] = Field(default=None, description="去重后已处理数")
    error_rows: Optional[int] = Field(default=None, description="错误行数")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    completed_at: Optional[datetime] = Field(default=None, description="完成时间")
    download_url: Optional[str] = Field(default=None, description="下载链接")
    error_message: Optional[str] = Field(default=None, description="错误信息")

class FileProcessingResponse(BaseModel):
    """文件处理响应"""
    success: bool = Field(..., description="是否成功")
    task: Optional[FileProcessingTask] = Field(default=None, description="处理任务")
    error: Optional[str] = Field(default=None, description="错误信息")

class TemplateInfo(BaseModel):
    """模板信息"""
    id: str = Field(..., description="模板ID")
    name: str = Field(..., description="模板名称")
    description: str = Field(..., description="模板描述")
    type: str = Field(..., description="模板类型（default/custom）")
    levels: List[Dict[str, str]] = Field(..., description="分类等级")

class TemplateListResponse(BaseModel):
    """模板列表响应"""
    success: bool = Field(..., description="是否成功")
    templates: List[TemplateInfo] = Field(default=[], description="模板列表")
    error: Optional[str] = Field(default=None, description="错误信息")

class SystemStatusResponse(BaseModel):
    """系统状态响应"""
    success: bool = Field(..., description="是否成功")
    status: Dict[str, Any] = Field(..., description="系统状态")
    error: Optional[str] = Field(default=None, description="错误信息")

class LLMConfigResponse(BaseModel):
    """LLM配置响应"""
    success: bool = Field(..., description="是否成功")
    config: Optional[Dict[str, Any]] = Field(default=None, description="当前配置")
    error: Optional[str] = Field(default=None, description="错误信息")

class LLMProfileInfo(BaseModel):
    """LLM配置档案信息"""
    name: str = Field(..., description="配置档案名称")
    config: Dict[str, Any] = Field(..., description="配置内容")
    is_active: bool = Field(..., description="是否为当前激活配置")

class LLMProfileListResponse(BaseModel):
    """LLM配置档案列表响应"""
    success: bool = Field(..., description="是否成功")
    profiles: List[LLMProfileInfo] = Field(default=[], description="配置档案列表")
    active_profile: Optional[str] = Field(default=None, description="当前激活配置")
    error: Optional[str] = Field(default=None, description="错误信息")

class LLMTestResponse(BaseModel):
    """LLM测试响应"""
    success: bool = Field(..., description="是否成功")
    test_success: bool = Field(..., description="模型调用是否成功")
    provider: Optional[str] = Field(default=None, description="提供商")
    model: Optional[str] = Field(default=None, description="模型")
    response: Optional[str] = Field(default=None, description="模型响应")
    error: Optional[str] = Field(default=None, description="错误信息")

class CustomTemplateResponse(BaseModel):
    """自定义模板响应"""
    success: bool = Field(..., description="是否成功")
    template: Optional[TemplateInfo] = Field(default=None, description="模板信息")
    error: Optional[str] = Field(default=None, description="错误信息")

# 通用响应模型
class BaseResponse(BaseModel):
    """基础响应模型"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="响应消息")
    error: Optional[str] = Field(default=None, description="错误信息")
    timestamp: datetime = Field(default_factory=datetime.now, description="响应时间")
