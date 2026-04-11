"""
批量处理器 - 处理Excel/CSV文件的批量分类
"""

import asyncio
import time
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable, Tuple
import pandas as pd
from .classifier import UniversalClassifier

class BatchProcessor:
    """批量处理器"""
    
    def __init__(self, classifier: Optional[UniversalClassifier] = None):
        """
        初始化批量处理器
        
        Args:
            classifier: 分类器实例，如果为None则创建新实例
        """
        self.classifier = classifier or UniversalClassifier()
        self.progress_callback: Optional[Callable] = None
    
    def set_progress_callback(self, callback: Callable[[int, int, str], None]) -> None:
        """
        设置进度回调函数
        
        Args:
            callback: 回调函数，参数为(current, total, message)
        """
        self.progress_callback = callback
    
    def _report_progress(self, current: int, total: int, message: str = "") -> None:
        """报告进度"""
        if self.progress_callback:
            self.progress_callback(current, total, message)

    @staticmethod
    def _normalize_text(value: Any) -> str:
        """标准化单元格文本，便于去重"""
        if pd.isna(value):
            return ""
        return str(value).strip()
    
    async def process_dataframe_async(self,
                                    df: pd.DataFrame,
                                    job_column: str = "岗位名称",
                                    company_column: str = "公司名称",
                                    task_type: str = "risk_assessment",
                                    max_concurrent: int = 5) -> pd.DataFrame:
        """
        异步处理DataFrame
        
        Args:
            df: 输入数据框
            job_column: 岗位名称列名
            company_column: 公司名称列名
            task_type: 分类任务类型
            max_concurrent: 最大并发数
            
        Returns:
            处理后的数据框
        """
        # 验证列名
        if job_column not in df.columns:
            available_cols = list(df.columns)
            job_column = self._find_similar_column(job_column, available_cols)
            if not job_column:
                raise ValueError(f"找不到岗位列，可用列：{available_cols}")

        if company_column not in df.columns:
            df[company_column] = ""

        # 添加结果列
        df = df.copy()
        df["分类结果"] = ""
        df["分类理由"] = ""
        df["处理状态"] = ""
        df["处理时间"] = ""

        total_rows = len(df)
        row_to_key: Dict[int, Tuple[str, str]] = {}
        unique_inputs: Dict[Tuple[str, str], Dict[str, Any]] = {}

        for index, row in df.iterrows():
            job_title = self._normalize_text(row[job_column])
            company_name = self._normalize_text(row[company_column])

            if not job_title:
                df.at[index, "处理状态"] = "跳过：空岗位名称"
                continue

            key = (job_title, company_name)
            row_to_key[index] = key
            if key not in unique_inputs:
                unique_inputs[key] = {
                    "job_title": job_title,
                    "company_name": company_name,
                    "sample_index": index
                }

        total_unique = len(unique_inputs)
        self._report_progress(
            0,
            max(total_unique, 1),
            f"开始去重处理：原始 {total_rows} 行，去重后 {total_unique} 组"
        )

        unique_results: Dict[Tuple[str, str], Dict[str, Any]] = {}
        processed_unique = 0
        semaphore = asyncio.Semaphore(max_concurrent)

        async def process_unique(key: Tuple[str, str], job_title: str, company_name: str, sample_index: int) -> None:
            nonlocal processed_unique

            async with semaphore:
                start_time = time.time()
                result = await self.classifier.classify_async(
                    job_title=job_title,
                    company_name=company_name,
                    task_type=task_type
                )
                processing_time = time.time() - start_time

                unique_results[key] = {
                    "success": result["success"],
                    "classification": result.get("classification", ""),
                    "reason": result.get("reason", ""),
                    "error": result.get("error", ""),
                    "processing_time": processing_time,
                    "sample_index": sample_index
                }

                processed_unique += 1
                if result["success"]:
                    msg = f"去重处理 {processed_unique}/{total_unique}：第 {sample_index + 1} 行 -> {result.get('classification', '')}"
                else:
                    msg = f"去重处理 {processed_unique}/{total_unique} 失败：第 {sample_index + 1} 行 -> {result.get('error', '')}"
                self._report_progress(processed_unique, max(total_unique, 1), msg)

                await asyncio.sleep(0.1)

        tasks = [
            process_unique(key, item["job_title"], item["company_name"], item["sample_index"])
            for key, item in unique_inputs.items()
        ]
        await asyncio.gather(*tasks, return_exceptions=False)

        for index, key in row_to_key.items():
            result = unique_results.get(key)
            if not result:
                df.at[index, "处理状态"] = "错误：去重结果缺失"
                continue

            if result["success"]:
                df.at[index, "分类结果"] = result["classification"]
                df.at[index, "分类理由"] = result["reason"]
                df.at[index, "处理状态"] = "成功"
                df.at[index, "处理时间"] = f"{result['processing_time']:.2f}秒"
            else:
                df.at[index, "处理状态"] = f"失败：{result['error']}"
                df.at[index, "处理时间"] = f"{result['processing_time']:.2f}秒"

        success_count = len(df[df["处理状态"] == "成功"])
        error_count = len(df[df["处理状态"].str.contains("失败|错误", na=False)])
        skip_count = len(df[df["处理状态"].str.contains("跳过", na=False)])
        self._report_progress(
            max(total_unique, 1),
            max(total_unique, 1),
            f"处理完成：成功 {success_count} 条，失败 {error_count} 条，跳过 {skip_count} 条，LLM实际调用 {total_unique} 次"
        )

        return df
    
    def process_dataframe_sync(self,
                              df: pd.DataFrame,
                              job_column: str = "岗位名称",
                              company_column: str = "公司名称",
                              task_type: str = "risk_assessment") -> pd.DataFrame:
        """
        同步处理DataFrame
        
        Args:
            df: 输入数据框
            job_column: 岗位名称列名
            company_column: 公司名称列名
            task_type: 分类任务类型
            
        Returns:
            处理后的数据框
        """
        # 验证列名
        if job_column not in df.columns:
            available_cols = list(df.columns)
            job_column = self._find_similar_column(job_column, available_cols)
            if not job_column:
                raise ValueError(f"找不到岗位列，可用列：{available_cols}")

        if company_column not in df.columns:
            df[company_column] = ""

        # 添加结果列
        df = df.copy()
        df["分类结果"] = ""
        df["分类理由"] = ""
        df["处理状态"] = ""
        df["处理时间"] = ""

        total_rows = len(df)
        row_to_key: Dict[int, Tuple[str, str]] = {}
        unique_inputs: Dict[Tuple[str, str], Dict[str, Any]] = {}

        for index, row in df.iterrows():
            job_title = self._normalize_text(row[job_column])
            company_name = self._normalize_text(row[company_column])

            if not job_title:
                df.at[index, "处理状态"] = "跳过：空岗位名称"
                continue

            key = (job_title, company_name)
            row_to_key[index] = key
            if key not in unique_inputs:
                unique_inputs[key] = {
                    "job_title": job_title,
                    "company_name": company_name,
                    "sample_index": index
                }

        total_unique = len(unique_inputs)
        self._report_progress(
            0,
            max(total_unique, 1),
            f"开始去重处理：原始 {total_rows} 行，去重后 {total_unique} 组"
        )

        unique_results: Dict[Tuple[str, str], Dict[str, Any]] = {}
        processed_unique = 0

        for key, item in unique_inputs.items():
            start_time = time.time()
            result = self.classifier.classify_sync(
                job_title=item["job_title"],
                company_name=item["company_name"],
                task_type=task_type
            )
            processing_time = time.time() - start_time

            unique_results[key] = {
                "success": result["success"],
                "classification": result.get("classification", ""),
                "reason": result.get("reason", ""),
                "error": result.get("error", ""),
                "processing_time": processing_time,
                "sample_index": item["sample_index"]
            }
            processed_unique += 1

            if result["success"]:
                msg = f"去重处理 {processed_unique}/{total_unique}：第 {item['sample_index'] + 1} 行 -> {result.get('classification', '')}"
            else:
                msg = f"去重处理 {processed_unique}/{total_unique} 失败：第 {item['sample_index'] + 1} 行 -> {result.get('error', '')}"
            self._report_progress(processed_unique, max(total_unique, 1), msg)

            time.sleep(0.1)

        for index, key in row_to_key.items():
            result = unique_results.get(key)
            if not result:
                df.at[index, "处理状态"] = "错误：去重结果缺失"
                continue

            if result["success"]:
                df.at[index, "分类结果"] = result["classification"]
                df.at[index, "分类理由"] = result["reason"]
                df.at[index, "处理状态"] = "成功"
                df.at[index, "处理时间"] = f"{result['processing_time']:.2f}秒"
            else:
                df.at[index, "处理状态"] = f"失败：{result['error']}"
                df.at[index, "处理时间"] = f"{result['processing_time']:.2f}秒"

        success_count = len(df[df["处理状态"] == "成功"])
        error_count = len(df[df["处理状态"].str.contains("失败|错误", na=False)])
        skip_count = len(df[df["处理状态"].str.contains("跳过", na=False)])
        self._report_progress(
            max(total_unique, 1),
            max(total_unique, 1),
            f"处理完成：成功 {success_count} 条，失败 {error_count} 条，跳过 {skip_count} 条，LLM实际调用 {total_unique} 次"
        )

        return df
    
    async def process_file_async(self,
                               input_file: str,
                               output_file: str,
                               job_column: str = "岗位名称",
                               company_column: str = "公司名称",
                               task_type: str = "risk_assessment",
                               max_concurrent: int = 5) -> Dict[str, Any]:
        """
        异步处理文件
        
        Args:
            input_file: 输入文件路径
            output_file: 输出文件路径
            job_column: 岗位名称列名
            company_column: 公司名称列名
            task_type: 分类任务类型
            max_concurrent: 最大并发数
            
        Returns:
            处理结果统计
        """
        try:
            # 读取文件
            input_path = Path(input_file)
            if input_path.suffix.lower() in ['.xlsx', '.xls']:
                df = pd.read_excel(input_file)
            elif input_path.suffix.lower() == '.csv':
                df = pd.read_csv(input_file)
            else:
                raise ValueError(f"不支持的文件格式：{input_path.suffix}")
            
            self._report_progress(0, len(df), f"开始处理文件：{input_file}")
            
            # 处理数据
            result_df = await self.process_dataframe_async(
                df=df,
                job_column=job_column,
                company_column=company_column,
                task_type=task_type,
                max_concurrent=max_concurrent
            )
            
            # 保存结果
            output_path = Path(output_file)
            if output_path.suffix.lower() in ['.xlsx', '.xls']:
                result_df.to_excel(output_file, index=False)
            elif output_path.suffix.lower() == '.csv':
                result_df.to_csv(output_file, index=False, encoding='utf-8-sig')
            
            # 统计结果
            success_count = len(result_df[result_df["处理状态"] == "成功"])
            error_count = len(result_df[result_df["处理状态"].str.contains("失败|错误", na=False)])
            skip_count = len(result_df[result_df["处理状态"].str.contains("跳过", na=False)])
            
            return {
                "success": True,
                "input_file": input_file,
                "output_file": output_file,
                "total_count": len(result_df),
                "success_count": success_count,
                "error_count": error_count,
                "skip_count": skip_count,
                "task_type": task_type
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"文件处理失败：{str(e)}",
                "input_file": input_file,
                "output_file": output_file
            }
    
    def process_file_sync(self,
                         input_file: str,
                         output_file: str,
                         job_column: str = "岗位名称",
                         company_column: str = "公司名称",
                         task_type: str = "risk_assessment") -> Dict[str, Any]:
        """
        同步处理文件
        
        Args:
            input_file: 输入文件路径
            output_file: 输出文件路径
            job_column: 岗位名称列名
            company_column: 公司名称列名
            task_type: 分类任务类型
            
        Returns:
            处理结果统计
        """
        try:
            # 读取文件
            input_path = Path(input_file)
            if input_path.suffix.lower() in ['.xlsx', '.xls']:
                df = pd.read_excel(input_file)
            elif input_path.suffix.lower() == '.csv':
                df = pd.read_csv(input_file)
            else:
                raise ValueError(f"不支持的文件格式：{input_path.suffix}")
            
            self._report_progress(0, len(df), f"开始处理文件：{input_file}")
            
            # 处理数据
            result_df = self.process_dataframe_sync(
                df=df,
                job_column=job_column,
                company_column=company_column,
                task_type=task_type
            )
            
            # 保存结果
            output_path = Path(output_file)
            if output_path.suffix.lower() in ['.xlsx', '.xls']:
                result_df.to_excel(output_file, index=False)
            elif output_path.suffix.lower() == '.csv':
                result_df.to_csv(output_file, index=False, encoding='utf-8-sig')
            
            # 统计结果
            success_count = len(result_df[result_df["处理状态"] == "成功"])
            error_count = len(result_df[result_df["处理状态"].str.contains("失败|错误", na=False)])
            skip_count = len(result_df[result_df["处理状态"].str.contains("跳过", na=False)])
            
            return {
                "success": True,
                "input_file": input_file,
                "output_file": output_file,
                "total_count": len(result_df),
                "success_count": success_count,
                "error_count": error_count,
                "skip_count": skip_count,
                "task_type": task_type
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"文件处理失败：{str(e)}",
                "input_file": input_file,
                "output_file": output_file
            }
    
    def _find_similar_column(self, target: str, available: List[str]) -> Optional[str]:
        """查找相似的列名"""
        # 精确匹配（忽略大小写）
        for col in available:
            if col.lower().strip() == target.lower().strip():
                return col
        
        # 岗位名称匹配
        job_keywords = ["岗位", "职位", "工作", "职业", "职务", "job", "position", "title", "role"]
        if any(keyword in target.lower() for keyword in job_keywords):
            for col in available:
                if any(keyword in col.lower() for keyword in job_keywords):
                    return col
        
        # 公司名称匹配
        company_keywords = ["公司", "企业", "单位", "机构", "组织", "company", "corp", "inc", "ltd"]
        if any(keyword in target.lower() for keyword in company_keywords):
            for col in available:
                if any(keyword in col.lower() for keyword in company_keywords):
                    return col
        
        # 模糊匹配（包含关系）
        for col in available:
            if target.lower() in col.lower() or col.lower() in target.lower():
                return col
        
        return None
