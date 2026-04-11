#!/usr/bin/env python3
"""
稳定的 JSON 输出 CLI 入口 (Phase 7B)
专供 AgentD 或者其他批处理系统调用
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# 禁止第三方库向 stdout 打印不需要的信息，保证 stdout 纯净输出 JSON
logging.basicConfig(stream=sys.stderr, level=logging.INFO)

# 添加src目录到Python路径
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.core.classifier import UniversalClassifier
from src.core.processor import BatchProcessor

async def classify_single(job_title: str, company_name: str, task_type: str):
    classifier = UniversalClassifier()
    try:
        result = await classifier.classify_async(job_title, company_name, task_type)
        if result["success"]:
            output = {
                "success": True,
                "task": task_type,
                "input": {"job_title": job_title, "company_name": company_name},
                "classification": result["classification"],
                "reason": result.get("reason", ""),
            }
            print(json.dumps(output, ensure_ascii=False))
            sys.exit(0)
        else:
            sys.stderr.write(f"Classification failed: {result['error']}\n")
            sys.exit(1)
    except Exception as e:
        sys.stderr.write(f"System error: {e}\n")
        sys.exit(1)

def progress_callback(current: int, total: int, message: str):
    sys.stderr.write(f"Progress: {current}/{total} - {message}\n")
    sys.stderr.flush()

async def process_file(input_file: str, output_file: str, task_type: str, job_column: str, company_column: str):
    processor = BatchProcessor()
    processor.set_progress_callback(progress_callback)
    
    try:
        result = await processor.process_file_async(
            input_file=input_file,
            output_file=output_file,
            task_type=task_type,
            job_column=job_column,
            company_column=company_column
        )
        if result["success"]:
            output = {
                "success": True,
                "task": task_type,
                "input": input_file,
                "output": output_file,
                "total": result.get("total_count", 0),
                "success_count": result.get("success_count", 0),
                "error_count": result.get("error_count", 0),
                "skip_count": result.get("skip_count", 0)
            }
            print(json.dumps(output, ensure_ascii=False))
            sys.exit(0)
        else:
            sys.stderr.write(f"Batch processing failed: {result['error']}\n")
            sys.exit(1)
    except Exception as e:
        sys.stderr.write(f"System error: {e}\n")
        sys.exit(1)

def health_check():
    classifier = UniversalClassifier()
    try:
        result = classifier.test_connection()
        output = {
            "success": result["success"],
            "provider": result.get("provider", "unknown"),
            "model": result.get("model", "unknown")
        }
        print(json.dumps(output, ensure_ascii=False))
        if result["success"]:
            sys.exit(0)
        else:
            sys.exit(1)
    except Exception as e:
        sys.stderr.write(f"Health check error: {e}\n")
        sys.exit(1)

def list_tasks():
    classifier = UniversalClassifier()
    try:
        tasks = classifier.get_available_tasks()
        output = {
            "success": True,
            "tasks": tasks
        }
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(0)
    except Exception as e:
        sys.stderr.write(f"List tasks error: {e}\n")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Employee Risk Classifier - CLI entrypoint")
    subparsers = parser.add_subparsers(dest="command", required=True)

    single_parser = subparsers.add_parser("classify-single", help="Classify a single job profile")
    single_parser.add_argument("--job-title", required=True, help="Job title")
    single_parser.add_argument("--company-name", default="", help="Company name (optional)")
    single_parser.add_argument("--task", default="risk_assessment", help="Task type")

    batch_parser = subparsers.add_parser("classify-file", help="Classify a batch file")
    batch_parser.add_argument("--input", required=True, help="Input file path (.xlsx/.csv)")
    batch_parser.add_argument("--output", required=True, help="Output file path (.xlsx/.csv)")
    batch_parser.add_argument("--job-column", default="岗位名称", help="Job title column name")
    batch_parser.add_argument("--company-column", default="公司名称", help="Company name column name")
    batch_parser.add_argument("--task", default="risk_assessment", help="Task type")

    subparsers.add_parser("health", help="Check LLM connection health")
    subparsers.add_parser("list-tasks", help="List available tasks")

    args = parser.parse_args()

    if args.command == "classify-single":
        asyncio.run(classify_single(args.job_title, args.company_name, args.task))
    elif args.command == "classify-file":
        asyncio.run(process_file(args.input, args.output, args.task, args.job_column, args.company_column))
    elif args.command == "health":
        health_check()
    elif args.command == "list-tasks":
        list_tasks()

if __name__ == "__main__":
    main()
