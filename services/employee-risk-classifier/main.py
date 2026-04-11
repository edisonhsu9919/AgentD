#!/usr/bin/env python3
"""
智能分类器主程序
支持命令行模式和API服务模式
"""

import argparse
import asyncio
import sys
from pathlib import Path

# 添加src目录到Python路径
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.core.classifier import UniversalClassifier
from src.core.processor import BatchProcessor
from src.api.app import app
from config.settings import settings

def progress_callback(current: int, total: int, message: str):
    """进度回调函数"""
    percentage = (current / total) * 100 if total > 0 else 0
    print(f"\r进度: {current}/{total} ({percentage:.1f}%) - {message}", end="", flush=True)
    if current == total:
        print()  # 换行

async def classify_single(job_title: str, company_name: str = "", task_type: str = "risk_assessment"):
    """单条分类"""
    classifier = UniversalClassifier()
    result = await classifier.classify_async(job_title, company_name, task_type)
    
    if result["success"]:
        print(f"岗位: {job_title}")
        if company_name:
            print(f"公司: {company_name}")
        print(f"分类结果: {result['classification']}")
        print(f"理由: {result['reason']}")
        print(f"使用模型: {result['provider']} - {result['model']}")
    else:
        print(f"分类失败: {result['error']}")

async def process_file(input_file: str, output_file: str, job_column: str, company_column: str, task_type: str):
    """处理文件"""
    processor = BatchProcessor()
    processor.set_progress_callback(progress_callback)
    
    print(f"开始处理文件: {input_file}")
    print(f"输出文件: {output_file}")
    print(f"任务类型: {task_type}")
    print("-" * 50)
    
    result = await processor.process_file_async(
        input_file=input_file,
        output_file=output_file,
        job_column=job_column,
        company_column=company_column,
        task_type=task_type
    )
    
    print("-" * 50)
    if result["success"]:
        print("✅ 处理完成!")
        print(f"总数: {result['total_count']}")
        print(f"成功: {result['success_count']}")
        print(f"失败: {result['error_count']}")
        print(f"跳过: {result['skip_count']}")
    else:
        print(f"❌ 处理失败: {result['error']}")

def start_server():
    """启动API服务器"""
    import uvicorn
    print(f"启动 {settings.app_name} API服务器...")
    print(f"访问地址: http://{settings.host}:{settings.port}")
    print(f"API文档: http://{settings.host}:{settings.port}/docs")
    
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        reload=settings.debug
    )

def list_tasks():
    """列出可用的分类任务"""
    classifier = UniversalClassifier()
    tasks = classifier.get_available_tasks()
    
    print("可用的分类任务:")
    print("-" * 30)
    for task_id, description in tasks.items():
        print(f"{task_id}: {description}")

def test_connection():
    """测试LLM连接"""
    classifier = UniversalClassifier()
    result = classifier.test_connection()
    
    print("LLM连接测试结果:")
    print("-" * 30)
    print(f"提供商: {result['provider']}")
    print(f"模型: {result['model']}")
    print(f"状态: {'✅ 连接成功' if result['success'] else '❌ 连接失败'}")
    
    if result['success']:
        print(f"响应: {result['response']}")
    else:
        print(f"错误: {result['error']}")

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="智能职业分类器")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    
    # 单条分类命令
    single_parser = subparsers.add_parser("single", help="单条分类")
    single_parser.add_argument("job_title", help="岗位名称")
    single_parser.add_argument("-c", "--company", default="", help="公司名称")
    single_parser.add_argument("-t", "--task", default="risk_assessment", help="分类任务类型")
    
    # 批量处理命令
    batch_parser = subparsers.add_parser("batch", help="批量处理文件")
    batch_parser.add_argument("input_file", help="输入文件路径")
    batch_parser.add_argument("output_file", help="输出文件路径")
    batch_parser.add_argument("-j", "--job-column", default="岗位名称", help="岗位名称列名")
    batch_parser.add_argument("-c", "--company-column", default="公司名称", help="公司名称列名")
    batch_parser.add_argument("-t", "--task", default="risk_assessment", help="分类任务类型")
    
    # 启动服务器命令
    subparsers.add_parser("server", help="启动API服务器")
    
    # 列出任务命令
    subparsers.add_parser("tasks", help="列出可用的分类任务")
    
    # 测试连接命令
    subparsers.add_parser("test", help="测试LLM连接")
    
    args = parser.parse_args()
    
    if args.command == "single":
        asyncio.run(classify_single(args.job_title, args.company, args.task))
    elif args.command == "batch":
        asyncio.run(process_file(
            args.input_file, 
            args.output_file, 
            args.job_column, 
            args.company_column, 
            args.task
        ))
    elif args.command == "server":
        start_server()
    elif args.command == "tasks":
        list_tasks()
    elif args.command == "test":
        test_connection()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()