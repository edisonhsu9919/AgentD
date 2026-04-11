"""
提示词模块 - 支持多种分类任务的提示词管理
"""

from .manager import PromptManager
from .templates import PromptTemplate, ClassificationTask
from .risk_assessment import RiskAssessmentPrompt
from .industry_classification import IndustryClassificationPrompt
from .skill_analysis import SkillAnalysisPrompt

__all__ = [
    "PromptManager",
    "PromptTemplate",
    "ClassificationTask",
    "RiskAssessmentPrompt",
    "IndustryClassificationPrompt", 
    "SkillAnalysisPrompt"
]