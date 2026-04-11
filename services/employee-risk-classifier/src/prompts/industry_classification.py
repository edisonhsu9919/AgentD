"""
行业分类提示词模板
"""

import re
from typing import Dict, List
from .templates import PromptTemplate, ClassificationTask, ClassificationLevel

class IndustryClassificationPrompt(PromptTemplate):
    """行业分类提示词"""
    
    def __init__(self):
        super().__init__(ClassificationTask.INDUSTRY_CLASSIFICATION)
        self._init_classification_levels()
    
    def _init_classification_levels(self):
        """初始化行业分类等级"""
        self.classification_levels = [
            ClassificationLevel(
                id="A类",
                name="农、林、牧、渔业",
                description="从事农业、林业、畜牧业、渔业相关工作",
                risk_characteristics=["自然环境作业", "季节性强", "体力劳动"],
                typical_jobs=["农民", "林业工人", "牧民", "渔民", "农业技术员", "兽医", "园艺师"]
            ),
            ClassificationLevel(
                id="B类",
                name="采矿业",
                description="从事煤炭、石油、天然气、金属矿物等开采工作",
                risk_characteristics=["高危作业", "地下作业", "爆破风险"],
                typical_jobs=["采矿工", "钻井工", "爆破员", "地质勘探员", "矿物加工工"]
            ),
            ClassificationLevel(
                id="C类",
                name="制造业",
                description="从事各类产品制造、加工、装配工作",
                risk_characteristics=["机械操作", "流水线作业", "精密加工"],
                typical_jobs=["机械工程师", "装配工", "质量检验员", "设备维修工", "生产主管"]
            ),
            ClassificationLevel(
                id="D类",
                name="电力、热力、燃气及水生产和供应业",
                description="从事电力、燃气、供水等公用事业",
                risk_characteristics=["高压作业", "特种设备", "公共安全"],
                typical_jobs=["电力工程师", "燃气检修工", "水处理工", "电网调度员"]
            ),
            ClassificationLevel(
                id="E类",
                name="建筑业",
                description="从事建筑工程施工、装修、安装工作",
                risk_characteristics=["高空作业", "重体力劳动", "施工风险"],
                typical_jobs=["建筑工人", "工程师", "项目经理", "监理员", "装修工"]
            ),
            ClassificationLevel(
                id="F类",
                name="批发和零售业",
                description="从事商品批发、零售、销售工作",
                risk_characteristics=["客户服务", "库存管理", "销售压力"],
                typical_jobs=["销售员", "店长", "采购员", "收银员", "仓管员"]
            ),
            ClassificationLevel(
                id="G类",
                name="交通运输、仓储和邮政业",
                description="从事运输、物流、邮政快递工作",
                risk_characteristics=["交通风险", "货物搬运", "时效要求"],
                typical_jobs=["司机", "快递员", "仓库管理员", "物流调度员", "装卸工"]
            ),
            ClassificationLevel(
                id="H类",
                name="住宿和餐饮业",
                description="从事酒店、餐厅、旅游服务工作",
                risk_characteristics=["服务导向", "节假日工作", "客户互动"],
                typical_jobs=["服务员", "厨师", "酒店管理员", "导游", "前台接待"]
            ),
            ClassificationLevel(
                id="I类",
                name="信息传输、软件和信息技术服务业",
                description="从事IT、软件开发、通信技术工作",
                risk_characteristics=["技术密集", "持续学习", "脑力劳动"],
                typical_jobs=["程序员", "系统分析师", "网络工程师", "产品经理", "UI设计师"]
            ),
            ClassificationLevel(
                id="J类",
                name="金融业",
                description="从事银行、保险、证券、投资等金融服务",
                risk_characteristics=["资金安全", "风险控制", "客户信任"],
                typical_jobs=["银行职员", "保险代理", "投资顾问", "会计师", "风控专员"]
            ),
            ClassificationLevel(
                id="K类",
                name="房地产业",
                description="从事房地产开发、销售、中介、物业管理",
                risk_characteristics=["市场波动", "大额交易", "客户关系"],
                typical_jobs=["房产销售", "物业经理", "房产中介", "房地产评估师"]
            ),
            ClassificationLevel(
                id="L类",
                name="租赁和商务服务业",
                description="从事租赁、咨询、商务服务等专业服务",
                risk_characteristics=["专业服务", "客户导向", "灵活就业"],
                typical_jobs=["管理咨询师", "人力资源专员", "法律顾问", "会展策划"]
            ),
            ClassificationLevel(
                id="M类",
                name="科学研究和技术服务业",
                description="从事科研、技术开发、检测认证等工作",
                risk_characteristics=["研究导向", "技术创新", "实验风险"],
                typical_jobs=["科研人员", "技术工程师", "实验员", "质量检测员"]
            ),
            ClassificationLevel(
                id="N类",
                name="水利、环境和公共设施管理业",
                description="从事环境保护、公共设施维护管理",
                risk_characteristics=["公共服务", "环境保护", "设施维护"],
                typical_jobs=["环保工程师", "污水处理工", "园林工人", "环境监测员"]
            ),
            ClassificationLevel(
                id="O类",
                name="居民服务、修理和其他服务业",
                description="从事生活服务、维修、个人服务等工作",
                risk_characteristics=["生活服务", "技能型工作", "客户接触"],
                typical_jobs=["理发师", "维修工", "保洁员", "家政服务员", "美容师"]
            ),
            ClassificationLevel(
                id="P类",
                name="教育",
                description="从事各级各类教育教学工作",
                risk_characteristics=["教书育人", "知识传授", "学生管理"],
                typical_jobs=["教师", "教授", "教务员", "培训师", "教育管理员"]
            ),
            ClassificationLevel(
                id="Q类",
                name="卫生和社会工作",
                description="从事医疗卫生、社会保障、社会救助工作",
                risk_characteristics=["生命健康", "社会责任", "专业风险"],
                typical_jobs=["医生", "护士", "社会工作者", "心理咨询师", "康复师"]
            ),
            ClassificationLevel(
                id="R类",
                name="文化、体育和娱乐业",
                description="从事文化艺术、体育、娱乐等工作",
                risk_characteristics=["创意工作", "表演艺术", "娱乐服务"],
                typical_jobs=["演员", "运动员", "记者", "编辑", "导演", "体育教练"]
            ),
            ClassificationLevel(
                id="S类",
                name="公共管理、社会保障和社会组织",
                description="从事政府管理、社会保障、社会组织工作",
                risk_characteristics=["公共服务", "政策执行", "社会管理"],
                typical_jobs=["公务员", "社保专员", "社区工作者", "政府职员"]
            ),
            ClassificationLevel(
                id="T类",
                name="国际组织",
                description="在国际组织中工作",
                risk_characteristics=["国际合作", "多元文化", "外语要求"],
                typical_jobs=["国际组织职员", "外交官", "国际项目协调员"]
            )
        ]
    
    def get_system_prompt(self) -> str:
        """获取系统提示词"""
        levels_text = "\n\n".join([
            f"{level.id}：{level.name}\n"
            f"\t定义：{level.description}\n"
            f"\t特征：{'、'.join(level.risk_characteristics)}\n"
            f"\t典型岗位：{'、'.join(level.typical_jobs[:5])}" + ("等" if len(level.typical_jobs) > 5 else "")
            for level in self.classification_levels
        ])
        
        return f"""
请根据岗位名称和所属公司判断该岗位所属的行业分类，并说明理由。
你是一个具有专业知识的行业分类助手。请根据国民经济行业分类标准（GB/T 4754-2017），对输入的岗位进行行业归类。

行业分类标准（20个门类）：

{levels_text}

输出格式请严格遵循：
行业分类：A类-T类中的一种，只能选择其一
理由：简要说明判断依据，控制在100字以内。

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
            class_pattern = r"行业分类：([A-T]类)"
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