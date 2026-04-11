"""
通用分类器 - 核心分类逻辑
"""

import time
from typing import Dict, Any, Optional
from ..llm.client import LLMClient
from ..llm.models import LLMMessage
from ..prompts.manager import prompt_manager

class UniversalClassifier:
    """通用职业分类器"""
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        """
        初始化分类器
        
        Args:
            llm_client: LLM客户端，如果为None则使用默认配置
        """
        self.llm_client = llm_client or LLMClient()
        self.prompt_manager = prompt_manager
    
    async def classify_async(self, 
                           job_title: str, 
                           company_name: str = "",
                           task_type: str = "risk_assessment") -> Dict[str, Any]:
        """
        异步分类职业
        
        Args:
            job_title: 岗位名称
            company_name: 公司名称
            task_type: 分类任务类型或自定义模板名称
            
        Returns:
            分类结果字典
        """
        try:
            # 获取提示词和格式化输入
            prompt_data = self.prompt_manager.classify_with_template(
                task_or_name=task_type,
                job_title=job_title,
                company_name=company_name
            )
            
            # 创建消息
            messages = [
                LLMMessage(role="system", content=prompt_data["system_prompt"]),
                LLMMessage(role="user", content=prompt_data["user_input"])
            ]
            
            # 调用LLM
            response = await self.llm_client.generate(messages)
            
            if not response.is_success:
                return {
                    "success": False,
                    "error": response.error or "LLM调用失败",
                    "classification": "",
                    "reason": ""
                }
            
            # 解析响应
            parsed_result = self.prompt_manager.parse_classification_response(
                task_or_name=task_type,
                response=response.content
            )
            
            return {
                "success": True,
                "classification": parsed_result.get("classification", "未分类"),
                "reason": parsed_result.get("reason", "未提供理由"),
                "raw_response": response.content,
                "provider": response.provider,
                "model": response.model,
                "usage": response.usage
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"分类过程中出现错误: {str(e)}",
                "classification": "",
                "reason": ""
            }
    
    def classify_sync(self, 
                     job_title: str, 
                     company_name: str = "",
                     task_type: str = "risk_assessment") -> Dict[str, Any]:
        """
        同步分类职业
        
        Args:
            job_title: 岗位名称
            company_name: 公司名称
            task_type: 分类任务类型或自定义模板名称
            
        Returns:
            分类结果字典
        """
        try:
            # 获取提示词和格式化输入
            prompt_data = self.prompt_manager.classify_with_template(
                task_or_name=task_type,
                job_title=job_title,
                company_name=company_name
            )
            
            # 创建消息
            messages = [
                LLMMessage(role="system", content=prompt_data["system_prompt"]),
                LLMMessage(role="user", content=prompt_data["user_input"])
            ]
            
            # 调用LLM
            response = self.llm_client.generate_sync(messages)
            
            if not response.is_success:
                return {
                    "success": False,
                    "error": response.error or "LLM调用失败",
                    "classification": "",
                    "reason": ""
                }
            
            # 解析响应
            parsed_result = self.prompt_manager.parse_classification_response(
                task_or_name=task_type,
                response=response.content
            )
            
            return {
                "success": True,
                "classification": parsed_result.get("classification", "未分类"),
                "reason": parsed_result.get("reason", "未提供理由"),
                "raw_response": response.content,
                "provider": response.provider,
                "model": response.model,
                "usage": response.usage
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"分类过程中出现错误: {str(e)}",
                "classification": "",
                "reason": ""
            }
    
    def get_available_tasks(self) -> Dict[str, str]:
        """获取可用的分类任务"""
        return self.prompt_manager.list_templates()
    
    def get_task_info(self, task_type: str) -> Optional[Dict]:
        """获取任务详细信息"""
        return self.prompt_manager.get_template_info(task_type)
    
    def update_llm_client(self, llm_client: LLMClient) -> None:
        """更新LLM客户端"""
        self.llm_client = llm_client
    
    def test_connection(self) -> Dict[str, Any]:
        """测试LLM连接"""
        try:
            test_messages = [
                LLMMessage(role="system", content="你是一个测试助手。"),
                LLMMessage(role="user", content="请回复'测试成功'。")
            ]
            
            response = self.llm_client.generate_sync(test_messages)
            
            return {
                "success": response.is_success,
                "response": response.content if response.is_success else None,
                "error": response.error,
                "provider": response.provider,
                "model": response.model
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"连接测试失败: {str(e)}",
                "provider": self.llm_client.config.provider.value,
                "model": self.llm_client.config.model
            }