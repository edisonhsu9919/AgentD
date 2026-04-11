"""
职业风险评估提示词模板
"""

import re
from typing import Dict, List
from .templates import PromptTemplate, ClassificationTask, ClassificationLevel

class RiskAssessmentPrompt(PromptTemplate):
    """职业风险评估提示词"""
    
    def __init__(self):
        super().__init__(ClassificationTask.RISK_ASSESSMENT)
        self._init_classification_levels()
    
    def _init_classification_levels(self):
        """初始化风险等级定义"""
        self.classification_levels = [
            ClassificationLevel(
                id="1类",
                name="极低风险 / 办公室岗位",
                description="完全在办公室工作，不接触生产线、设备或高风险环境",
                risk_characteristics=["无机械作业", "无体力劳动", "无接触危险物"],
                typical_jobs=["文员", "财务", "人事", "行政助理", "市场策划", "公关专员", "办公室高管", "总经理（不亲自参与生产）"]
            ),
            ClassificationLevel(
                id="2类",
                name="低风险 / 辅助性或轻微接触型岗位",
                description="在室内或规范环境下从事辅助工作，可能有少量外勤或轻设备操作",
                risk_characteristics=["接触风险可控", "劳动强度小"],
                typical_jobs=["外勤人员（客户拜访、资料交接）", "实验动物饲养", "兽医", "动物检疫员", "实验室技术员", "资料录入", "沼气管理人员"]
            ),
            ClassificationLevel(
                id="3类", 
                name="中等风险 / 农业、实验性及自然作业类",
                description="工作场景为农业、林牧渔等自然环境或接触生物有机物",
                risk_characteristics=["暴露风险", "环境不稳定", "部分劳动强度大"],
                typical_jobs=["畜牧工", "农业技术员", "牧草加工", "渔场经营者", "养殖工人（内陆）", "农用设备驾驶员"]
            ),
            ClassificationLevel(
                id="4类",
                name="中高风险 / 一般工厂及基础危险岗位", 
                description="在工厂、食品、物流、建筑辅助等场景中，存在明显的劳动伤害风险",
                risk_characteristics=["接触机械", "高温作业", "重体力劳动"],
                typical_jobs=["食品加工", "下料工", "装卸搬运工", "油炸员", "饮料灌装工", "调味品发酵员", "罐头整理员", "内陆捕鱼人", "水煤浆制备员", "清洁工", "保洁"]
            ),
            ClassificationLevel(
                id="5类",
                name="高风险 / 有毒、有害、化工类岗位",
                description="作业过程涉及不稳定化学物、特殊养殖、有毒材料等",
                risk_characteristics=["操作失误可导致中毒", "灼伤或系统性伤害"],
                typical_jobs=["工业型煤加工", "油罐清洗", "漆包线工", "沼气工程施工员", "木工旋切工", "特种养殖（蜂、蛇、鳄鱼）"]
            ),
            ClassificationLevel(
                id="6类",
                name="极高风险 / 矿井、爆破、液化气及高空作业",
                description="处于高度危险环境，可能涉及易燃易爆、高空、高压或重型机械",
                risk_characteristics=["职业伤害概率高", "有致残致死风险"],
                typical_jobs=["采矿工人", "钻孔机司机", "爆破员", "井下维修", "液化气/油罐车司机", "盐场采掘船员", "木材加工林工", "煤层气钻井员", "高空清洗员"]
            )
        ]
    
    def get_system_prompt(self) -> str:
        """获取系统提示词"""
        levels_text = "\n\n⸻\n\n".join([
            f"{level.id}：{level.name}\n"
            f"\t•\t定义：{level.description}；\n"
            f"\t•\t风险特征：{'、'.join(level.risk_characteristics)}；\n"
            f"\t•\t典型岗位：\n\t•\t" + '\n\t•\t'.join(level.typical_jobs)
            for level in self.classification_levels
        ])
        
        return f"""
请根据岗位名称和所属公司判断该岗位的职业风险等级（分为1~6类），并说明理由。
你是一个具有专业知识的岗位风险评估助手。请根据下列职业风险等级分类标准，对输入的岗位进行分析和归类。你的任务是判断岗位属于哪个风险等级（1-6类），并说明判断依据。标准分为六类，级数越高风险越大。

⸻

岗位风险等级分类标准

{levels_text}

{self.get_output_format_instruction()}
"""
    
    def format_user_input(self, job_title: str, company_name: str = "", **kwargs) -> str:
        """格式化用户输入"""
        if company_name:
            return f"{job_title}\n{company_name}"
        else:
            return job_title
    
    def parse_response(self, response: str) -> Dict[str, str]:
        """解析AI响应"""
        result = {"classification": "未分类", "reason": "解析失败"}
        
        try:
            # 提取分类结果
            class_pattern = r"职业类别：([1-6]类|1-3类)"
            class_match = re.search(class_pattern, response)
            if class_match:
                result["classification"] = class_match.group(1)
            else:
                # 兼容新格式
                class_pattern2 = r"分类结果：([1-6]类|1-3类)"
                class_match2 = re.search(class_pattern2, response)
                if class_match2:
                    result["classification"] = class_match2.group(1)
            
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
    
    def get_output_format_instruction(self) -> str:
        """获取输出格式说明"""
        return """
输出格式请严格遵循：
职业类别：1-3类 或 4类 或 5类 或 6类，只能选择其一
理由：<理由>简要说明，控制在100字以内。

/no_think
"""