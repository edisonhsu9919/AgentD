"""
提示词管理器
"""

from typing import Dict, Optional, List
from .templates import PromptTemplate, ClassificationTask, CustomPromptTemplate, ClassificationLevel
from .risk_assessment import RiskAssessmentPrompt
from .industry_classification import IndustryClassificationPrompt
from .skill_analysis import SkillAnalysisPrompt

class PromptManager:
    """提示词管理器 - 统一管理各种分类任务的提示词"""
    
    def __init__(self):
        self._templates: Dict[ClassificationTask, PromptTemplate] = {}
        self._custom_templates: Dict[str, CustomPromptTemplate] = {}
        self._init_default_templates()
    
    def _init_default_templates(self):
        """初始化默认模板"""
        self._templates[ClassificationTask.RISK_ASSESSMENT] = RiskAssessmentPrompt()
        self._templates[ClassificationTask.INDUSTRY_CLASSIFICATION] = IndustryClassificationPrompt()
        self._templates[ClassificationTask.SKILL_ANALYSIS] = SkillAnalysisPrompt()
    
    def get_template(self, task: ClassificationTask) -> Optional[PromptTemplate]:
        """获取指定任务的提示词模板"""
        return self._templates.get(task)
    
    def get_custom_template(self, name: str) -> Optional[CustomPromptTemplate]:
        """获取自定义提示词模板"""
        return self._custom_templates.get(name)
    
    def add_custom_template(self, 
                          name: str,
                          task_name: str, 
                          system_prompt: str,
                          classification_levels: List[ClassificationLevel],
                          output_format: str = "分类结果：{classification}\n理由：{reason}") -> None:
        """
        添加自定义提示词模板
        
        Args:
            name: 模板名称（唯一标识）
            task_name: 任务名称
            system_prompt: 系统提示词
            classification_levels: 分类等级定义
            output_format: 输出格式
        """
        template = CustomPromptTemplate(
            task_name=task_name,
            system_prompt=system_prompt,
            classification_levels=classification_levels,
            output_format=output_format
        )
        self._custom_templates[name] = template
    
    def remove_custom_template(self, name: str) -> bool:
        """移除自定义提示词模板"""
        if name in self._custom_templates:
            del self._custom_templates[name]
            return True
        return False
    
    def list_templates(self) -> Dict[str, str]:
        """列出所有可用的模板"""
        templates = {}
        
        # 默认模板
        for task, template in self._templates.items():
            templates[task.value] = template.get_task_description()
        
        # 自定义模板
        for name, template in self._custom_templates.items():
            templates[f"custom_{name}"] = template.task_name
        
        return templates
    
    def get_template_info(self, task_or_name: str) -> Optional[Dict]:
        """获取模板详细信息"""
        # 尝试获取默认模板
        try:
            task = ClassificationTask(task_or_name)
            template = self._templates.get(task)
            if template:
                return {
                    "type": "default",
                    "task": task.value,
                    "description": template.get_task_description(),
                    "levels": [
                        {
                            "id": level.id,
                            "name": level.name,
                            "description": level.description
                        } for level in template.get_classification_levels()
                    ]
                }
        except ValueError:
            pass
        
        # 尝试获取自定义模板
        if task_or_name.startswith("custom_"):
            name = task_or_name[7:]  # 移除"custom_"前缀
            template = self._custom_templates.get(name)
            if template:
                return {
                    "type": "custom",
                    "name": name,
                    "task_name": template.task_name,
                    "system_prompt": template.system_prompt,
                    "output_format": template.output_format,
                    "levels": [
                        {
                            "id": level.id,
                            "name": level.name,
                            "description": level.description
                        } for level in template.get_classification_levels()
                    ]
                }
        
        return None
    
    def classify_with_template(self, 
                             task_or_name: str,
                             job_title: str,
                             company_name: str = "") -> Dict:
        """
        使用指定模板进行分类
        
        Args:
            task_or_name: 任务类型或自定义模板名称
            job_title: 岗位名称
            company_name: 公司名称
            
        Returns:
            包含system_prompt和user_input的字典
        """
        template = None
        
        # 尝试获取默认模板
        try:
            task = ClassificationTask(task_or_name)
            template = self._templates.get(task)
        except ValueError:
            pass
        
        # 尝试获取自定义模板
        if not template and task_or_name.startswith("custom_"):
            name = task_or_name[7:]
            template = self._custom_templates.get(name)
        
        if not template:
            raise ValueError(f"Template not found: {task_or_name}")
        
        return {
            "system_prompt": template.get_system_prompt(),
            "user_input": template.format_user_input(job_title, company_name),
            "template": template
        }
    
    def parse_classification_response(self, 
                                    task_or_name: str,
                                    response: str) -> Dict[str, str]:
        """
        解析分类响应
        
        Args:
            task_or_name: 任务类型或自定义模板名称
            response: AI响应内容
            
        Returns:
            解析后的分类结果
        """
        template = None
        
        # 尝试获取默认模板
        try:
            task = ClassificationTask(task_or_name)
            template = self._templates.get(task)
        except ValueError:
            pass
        
        # 尝试获取自定义模板
        if not template and task_or_name.startswith("custom_"):
            name = task_or_name[7:]
            template = self._custom_templates.get(name)
        
        if not template:
            raise ValueError(f"Template not found: {task_or_name}")
        
        return template.parse_response(response)

# 全局提示词管理器实例
prompt_manager = PromptManager()