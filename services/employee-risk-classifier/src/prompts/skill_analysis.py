"""
技能分析提示词模板
"""

import re
from typing import Dict, List
from .templates import PromptTemplate, ClassificationTask, ClassificationLevel

class SkillAnalysisPrompt(PromptTemplate):
    """技能分析提示词"""
    
    def __init__(self):
        super().__init__(ClassificationTask.SKILL_ANALYSIS)
        self._init_classification_levels()
    
    def _init_classification_levels(self):
        """初始化技能等级定义"""
        self.classification_levels = [
            ClassificationLevel(
                id="初级",
                name="初级技能要求",
                description="基础技能要求，容易掌握，培训周期短",
                risk_characteristics=["基础操作", "标准化流程", "监督指导"],
                typical_jobs=["前台接待", "数据录入员", "清洁工", "收银员", "保安", "服务员"]
            ),
            ClassificationLevel(
                id="中级",
                name="中级技能要求",
                description="需要一定专业技能和经验，培训周期中等",
                risk_characteristics=["专业技能", "独立操作", "经验积累"],
                typical_jobs=["销售代表", "会计", "技术员", "设备操作员", "客服专员", "行政助理"]
            ),
            ClassificationLevel(
                id="高级",
                name="高级技能要求",
                description="需要较高专业技能和丰富经验，培训周期长",
                risk_characteristics=["专业精通", "复杂问题解决", "团队协作"],
                typical_jobs=["工程师", "高级会计师", "项目经理", "高级技师", "业务主管", "专业顾问"]
            ),
            ClassificationLevel(
                id="专家级",
                name="专家级技能要求",
                description="需要专业领域的高深知识和丰富实践经验",
                risk_characteristics=["领域专家", "创新能力", "战略思维"],
                typical_jobs=["高级工程师", "首席技术官", "资深顾问", "科研人员", "专业导师", "技术专家"]
            ),
            ClassificationLevel(
                id="管理级",
                name="管理级技能要求",
                description="需要管理技能、领导力和战略规划能力",
                risk_characteristics=["团队管理", "决策能力", "战略规划"],
                typical_jobs=["部门经理", "总监", "CEO", "项目总监", "区域经理", "运营经理"]
            )
        ]
    
    def get_system_prompt(self) -> str:
        """获取系统提示词"""
        levels_text = "\n\n".join([
            f"{level.id}：{level.name}\n"
            f"\t定义：{level.description}\n"
            f"\t特征：{'、'.join(level.risk_characteristics)}\n"
            f"\t典型岗位：{'、'.join(level.typical_jobs)}"
            for level in self.classification_levels
        ])
        
        return f"""
请根据岗位名称和所属公司分析该岗位的技能要求等级，并说明理由。
你是一个具有专业知识的岗位技能分析助手。请根据下列技能等级分类标准，对输入的岗位进行技能要求分析和归类。

技能等级分类标准：

{levels_text}

分析要点：
1. 考虑岗位所需的专业知识深度
2. 评估所需工作经验和培训周期
3. 分析工作复杂度和决策权限
4. 考虑团队协作和管理责任

输出格式请严格遵循：
技能等级：初级、中级、高级、专家级、管理级中的一种，只能选择其一
理由：简要说明技能要求分析，控制在100字以内。

/no_think
"""
    
    def format_user_input(self, job_title: str, company_name: str = "", **kwargs) -> str:
        """格式化用户输入"""
        if company_name:
            return f"岗位名称：{job_title}\n公司名称：{company_name}"
        else:
            return f"岗位名称：{job_title}"
    
    def parse_response(self, response: str) -> Dict[str, str]:
        """解析AI响应"""
        result = {"classification": "未分类", "reason": "解析失败"}
        
        try:
            # 提取分类结果
            class_pattern = r"技能等级：(初级|中级|高级|专家级|管理级)"
            class_match = re.search(class_pattern, response)
            if class_match:
                result["classification"] = class_match.group(1)
            
            # 提取理由
            reason_pattern = r"理由：(.+?)(?:\n|$)"
            reason_match = re.search(reason_pattern, response, re.DOTALL)
            if reason_match:
                result["reason"] = reason_match.group(1).strip()
            
            return result
        except Exception as e:
            return {"classification": "解析错误", "reason": f"输出解析失败: {str(e)}"}
    
    def get_classification_levels(self) -> List[ClassificationLevel]:
        """获取分类等级"""
        return self.classification_levels