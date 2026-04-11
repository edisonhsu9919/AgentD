"""
提示词模板基础类和枚举
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, Any, List, Optional
from pydantic import BaseModel

class ClassificationTask(str, Enum):
    """分类任务类型"""
    RISK_ASSESSMENT = "risk_assessment"
    INDUSTRY_CLASSIFICATION = "industry_classification"
    SKILL_ANALYSIS = "skill_analysis"
    CUSTOM = "custom"

class ClassificationLevel(BaseModel):
    """分类等级定义"""
    id: str
    name: str
    description: str
    risk_characteristics: List[str]
    typical_jobs: List[str]

class PromptTemplate(ABC):
    """提示词模板基类"""
    
    def __init__(self, task_type: ClassificationTask):
        self.task_type = task_type
        self.classification_levels: List[ClassificationLevel] = []
        self._system_prompt: Optional[str] = None
        
    @abstractmethod
    def get_system_prompt(self) -> str:
        """获取系统提示词"""
        pass
    
    @abstractmethod
    def format_user_input(self, job_title: str, company_name: str = "", **kwargs) -> str:
        """格式化用户输入"""
        pass
    
    @abstractmethod
    def parse_response(self, response: str) -> Dict[str, str]:
        """解析AI响应"""
        pass
    
    @abstractmethod
    def get_classification_levels(self) -> List[ClassificationLevel]:
        """获取分类等级定义"""
        pass
    
    def get_task_description(self) -> str:
        """获取任务描述"""
        return f"基于{self.task_type.value}进行智能分类"
    
    def validate_response(self, response: Dict[str, str]) -> bool:
        """验证响应格式是否正确"""
        required_fields = ["classification", "reason"]
        return all(field in response for field in required_fields)
    
    def get_output_format_instruction(self) -> str:
        """获取输出格式说明"""
        levels = self.get_classification_levels()
        level_options = " 或 ".join([f"{level.id}" for level in levels])
        
        return f"""
输出格式请严格遵循：
分类结果：{level_options}，只能选择其一
理由：<理由>简要说明，控制在100字以内。

/no_think
"""

class CustomPromptTemplate(PromptTemplate):
    """自定义提示词模板"""
    
    def __init__(self, 
                 task_name: str,
                 system_prompt: str,
                 classification_levels: List[ClassificationLevel],
                 output_format: str = "分类结果：{classification}\n理由：{reason}"):
        super().__init__(ClassificationTask.CUSTOM)
        self.task_name = task_name
        self._system_prompt = system_prompt
        self.classification_levels = classification_levels
        self.output_format = output_format
    
    def get_system_prompt(self) -> str:
        return self._system_prompt
    
    def format_user_input(self, job_title: str, company_name: str = "", **kwargs) -> str:
        if company_name:
            return f"岗位名称：{job_title}\n公司名称：{company_name}"
        else:
            return f"岗位名称：{job_title}"
    
    def parse_response(self, response: str) -> Dict[str, str]:
        """解析响应（通用解析器）"""
        result = {"classification": "未分类", "reason": "解析失败"}
        
        # 尝试解析分类结果
        import re
        class_pattern = r"分类结果：(.+?)(?:\n|$)"
        class_match = re.search(class_pattern, response)
        if class_match:
            result["classification"] = class_match.group(1).strip()
        
        # 尝试解析理由
        reason_pattern = r"理由：(.+?)(?:\n|$)"
        reason_match = re.search(reason_pattern, response, re.DOTALL)
        if reason_match:
            result["reason"] = reason_match.group(1).strip()
        
        return result
    
    def get_classification_levels(self) -> List[ClassificationLevel]:
        return self.classification_levels