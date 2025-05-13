#!/usr/bin/env python3
"""
AWD 自动化 Flag 提交工具
功能：
1. 支持文件输入（一行一个flag）或管道输入（空格/逗号分隔）
2. 完全解耦的目标服务器配置
3. 多线程并发提交
4. 完善的错误处理和日志
"""

import sys
import os
import re
import json
import time
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Dict, Optional
import requests
import yaml
import logging
from pythonjsonlogger import jsonlogger

# ==================== 配置 ====================
DEFAULT_CONFIG = """
# 默认目标服务器配置（可通过 --config 指定外部文件）
targets:
  - ip: "192.168.1.100"
    port: 80
    path: "/submit.php"
    protocol: "http"
    timeout: 3
    headers:
      User-Agent: "AWD-Submitter/1.0"
  
  - ip: "10.0.0.2"
    port: 8080
    path: "/api/submit"
    protocol: "https"
    timeout: 5
"""

FLAG_REGEX = re.compile(r'flag{[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}}', re.I)  # 匹配标准flag格式

# ==================== 日志设置 ====================
def setup_logging():
    logger = logging.getLogger('AWDSubmitter')
    logger.setLevel(logging.INFO)
    
    formatter = jsonlogger.JsonFormatter(
        '%(asctime)s %(levelname)s %(message)s %(module)s %(funcName)s'
    )
    
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger

logger = setup_logging()

# ==================== 核心类 ====================
class AWDFlagSubmitter:
    def __init__(self, config_data: Dict):
        self.targets = config_data.get('targets', [])
        self.max_workers = config_data.get('max_workers', 5)
        self.max_retries = config_data.get('max_retries', 2)
        self.verify_ssl = config_data.get('verify_ssl', False)
        
        if not self.targets:
            logger.error("No valid targets configured", extra={'config': config_data})
            raise ValueError("至少需要配置一个目标服务器")

    def _validate_flag(self, flag: str) -> bool:
        """验证flag格式是否正确"""
        return bool(FLAG_REGEX.fullmatch(flag.strip()))

    def _read_flags_file(self, file_path: str) -> List[str]:
        """从文件读取flag（一行一个）"""
        try:
            with open(file_path, 'r') as f:
                return [line.strip() for line in f if self._validate_flag(line)]
        except Exception as e:
            logger.error("读取flag文件失败", extra={'file': file_path, 'error': str(e)})
            return []

    def _read_flags_stdin(self) -> List[str]:
        """从标准输入读取flag（支持空格/逗号分隔）"""
        if sys.stdin.isatty():  # 没有管道输入
            return []
        
        data = sys.stdin.read()
        flags = []
        for item in re.split(r'[,\s]+', data.strip()):
            if self._validate_flag(item):
                flags.append(item)
        return flags

    def get_flags(self, input_file: Optional[str] = None) -> List[str]:
        """获取flag列表（优先文件输入）"""
        if input_file and Path(input_file).exists():
            flags = self._read_flags_file(input_file)
            logger.info("从文件加载flag", extra={'count': len(flags), 'source': input_file})
        else:
            flags = self._read_flags_stdin()
            logger.info("从标准输入加载flag", extra={'count': len(flags)})
        
        if not flags:
            logger.warning("未获取到有效flag")
        return flags

    def submit_single(self, target: Dict, flag: str) -> Dict:
        """单次flag提交"""
        result = {
            'target': f"{target['ip']}:{target['port']}",
            'flag': flag[:8] + '...',  # 日志脱敏
            'success': False
        }
        
        url = f"{target['protocol']}://{target['ip']}:{target['port']}{target['path']}"
        headers = target.get('headers', {})
        
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(
                    url,
                    data={'flag': flag},
                    headers=headers,
                    timeout=target.get('timeout', 3),
                    verify=self.verify_ssl
                )
                
                result.update({
                    'status_code': resp.status_code,
                    'response': resp.text[:100],
                    'attempt': attempt,
                    'success': resp.status_code == 200
                })
                break
                
            except Exception as e:
                result['error'] = str(e)
                time.sleep(1)  # 失败后延迟
        
        logger.info("提交结果", extra=result)
        return result

    def submit_all(self, flags: List[str]) -> List[Dict]:
        """并发提交所有flag到所有目标"""
        if not flags:
            return []
            
        total_submissions = len(flags) * len(self.targets)
        logger.info("开始提交任务", extra={
            'flag_count': len(flags),
            'target_count': len(self.targets),
            'total_submissions': total_submissions
        })
        
        results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = []
            for flag in flags:
                for target in self.targets:
                    futures.append(executor.submit(
                        self.submit_single, target, flag
                    ))
            
            for future in futures:
                try:
                    results.append(future.result())
                except Exception as e:
                    logger.error("提交任务异常", extra={'error': str(e)})
        
        success_rate = sum(1 for r in results if r['success']) / len(results) * 100
        logger.info("提交任务完成", extra={
            'success_rate': f"{success_rate:.2f}%",
            'total_results': len(results)
        })
        return results

# ==================== 命令行接口 ====================
def parse_args():
    parser = argparse.ArgumentParser(description="AWD自动提交Flag工具")
    parser.add_argument('-c', '--config', help='YAML配置文件路径')
    parser.add_argument('-f', '--input-file', help='flag文件路径（一行一个）')
    parser.add_argument('-o', '--output', help='结果输出文件')
    parser.add_argument('-v', '--verbose', action='store_true', help='显示调试信息')
    return parser.parse_args()

def load_config(config_path: Optional[str]) -> Dict:
    """加载配置文件"""
    if config_path and Path(config_path).exists():
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
    
    logger.warning("使用内置默认配置")
    return yaml.safe_load(DEFAULT_CONFIG)

def main():
    args = parse_args()
    
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    
    try:
        # 1. 加载配置
        config = load_config(args.config)
        submitter = AWDFlagSubmitter(config)
        
        # 2. 获取flag
        flags = submitter.get_flags(args.input_file)
        if not flags:
            logger.error("未获取到有效flag，退出程序")
            return
        
        # 3. 提交flag
        results = submitter.submit_all(flags)
        
        # 4. 输出结果
        if args.output:
            with open(args.output, 'w') as f:
                json.dump(results, f, indent=2)
            logger.info("结果已保存到文件", extra={'path': args.output})
        
        # 显示统计信息
        success_count = sum(1 for r in results if r.get('success'))
        print(f"\n[统计] 成功: {success_count}/{len(results)} | 成功率: {success_count/len(results)*100:.1f}%")
        
    except Exception as e:
        logger.critical("程序运行异常", exc_info=True)
        sys.exit(1)

if __name__ == '__main__':
    main()

