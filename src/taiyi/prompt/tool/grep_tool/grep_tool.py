"""
Grep Tool - 继承自 Tool 基类的实现（使用原生grep命令）

基于系统原生grep的搜索工具。支持正则表达式、文件过滤、多种输出模式和常用搜索选项。

使用指南：
- 使用系统原生grep命令，无需额外安装
- 支持基本和扩展正则表达式语法
- 通过glob参数过滤文件（如"*.js"、"*.py"）
- 三种输出模式："content"显示匹配行，"files_with_matches"显示文件路径（默认），"count"显示匹配计数
- 支持递归搜索目录

高级选项：
- 上下文控制：-B显示匹配前行，-A显示匹配后行，-C显示前后行
- 行号显示：-n标志在内容模式下显示行号
- 大小写不敏感：-i标志进行不区分大小写的搜索
- 结果限制：head_limit参数限制输出条目数
- 扩展正则：use_extended_regexp启用扩展正则表达式（-E选项）

关键注意事项：
- path参数使用绝对路径，默认为当前工作目录
- pattern参数必需，使用grep正则表达式语法
- 递归搜索目录时自动使用-r选项
- 上下文选项仅在"content"输出模式下有效
"""

import os
import subprocess
import re
import json
import time
import fnmatch
from typing import Optional, Dict, Any, List
from pathlib import Path

from taiyi.core.tool_types import (
    Tool,
    ToolSchema,
    ParametersSchema,
    ToolRequest,
    ToolRequestResult
)


class GrepTool(Tool):
    """Grep工具类 - 继承自Tool基类，基于原生grep实现搜索功能"""

    @property
    def name(self) -> str:
        return "Grep"

    @property
    def description(self) -> str:
        return """
基于系统原生grep的搜索工具。支持正则表达式、文件过滤、多种输出模式和常用搜索选项。

使用指南：
- 使用系统原生grep命令，无需额外安装
- 支持基本和扩展正则表达式语法
- 通过glob参数过滤文件（如"*.js"、"*.py"）
- 三种输出模式："content"显示匹配行，"files_with_matches"显示文件路径（默认），"count"显示匹配计数
- 支持递归搜索目录

高级选项：
- 上下文控制：-B显示匹配前行，-A显示匹配后行，-C显示前后行
- 行号显示：-n标志在内容模式下显示行号
- 大小写不敏感：-i标志进行不区分大小写的搜索
- 结果限制：head_limit参数限制输出条目数
- 扩展正则：use_extended_regexp启用扩展正则表达式（-E选项）

关键注意事项：
- path参数使用绝对路径，默认为当前工作目录
- pattern参数必需，使用grep正则表达式语法
- 递归搜索目录时自动使用-r选项
- 上下文选项仅在"content"输出模式下有效
        """

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=ParametersSchema(
                type="object",
                properties={
                    "pattern": {
                        "type": "string",
                        "description": "要搜索的正则表达式模式（必需）"
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索的文件或目录路径（可选，默认当前工作目录，必须是绝对路径）"
                    },
                    "glob": {
                        "type": "string",
                        "description": "过滤文件的glob模式（如*.js, *.py），用于筛选文件"
                    },
                    "output_mode": {
                        "type": "string",
                        "enum": ["content", "files_with_matches", "count"],
                        "description": "输出模式：content显示匹配行，files_with_matches显示文件路径（默认），count显示匹配计数"
                    },
                    "-B": {
                        "type": "integer",
                        "description": "匹配前显示的行数（仅在output_mode为content时有效）"
                    },
                    "-A": {
                        "type": "integer",
                        "description": "匹配后显示的行数（仅在output_mode为content时有效）"
                    },
                    "-C": {
                        "type": "integer",
                        "description": "匹配前后显示的行数（仅在output_mode为content时有效）"
                    },
                    "-n": {
                        "type": "boolean",
                        "description": "显示行号（仅在output_mode为content时有效）"
                    },
                    "-i": {
                        "type": "boolean",
                        "description": "大小写不敏感搜索"
                    },
                    "head_limit": {
                        "type": "integer",
                        "description": "限制输出前N行/条目，适用于所有输出模式"
                    },
                    "use_extended_regexp": {
                        "type": "boolean",
                        "description": "使用扩展正则表达式（grep -E），默认false"
                    }
                },
                required=["pattern"]
            ),
            strict=False
        )

    def __init__(self):
        """初始化GrepTool"""
        self.default_working_dir = os.getcwd()
        # 每批处理的最大文件数，避免参数列表过长
        self.batch_size = 100

    async def execute(self, tool_request: ToolRequest) -> ToolRequestResult:
        """执行Grep工具"""
        start_time = time.time()

        # 从ToolRequest中提取参数
        if isinstance(tool_request.tool_call_arguments, str):
            call_args = self.parse_string_arguments(tool_request.tool_call_arguments)
        else:
            call_args = tool_request.tool_call_arguments

        # 验证必需参数
        if "pattern" not in call_args:
            execution_time = time.time() - start_time
            error_result = {"error": "Missing required parameter: pattern"}
            return ToolRequestResult(
                request=tool_request,
                result=error_result,
                content=json.dumps(error_result, ensure_ascii=False),
                is_error=True,
                execution_times=execution_time
            )

        # 执行搜索操作
        try:
            result = self._search(
                pattern=call_args["pattern"],
                path=call_args.get("path"),
                glob=call_args.get("glob"),
                output_mode=call_args.get("output_mode", "files_with_matches"),
                context_before=call_args.get("-B"),
                context_after=call_args.get("-A"),
                context_around=call_args.get("-C"),
                show_line_numbers=call_args.get("-n"),
                case_insensitive=call_args.get("-i"),
                head_limit=call_args.get("head_limit"),
                use_extended_regexp=call_args.get("use_extended_regexp", False)
            )

            execution_time = time.time() - start_time

            if result.get("success"):
                return ToolRequestResult(
                    request=tool_request,
                    result=result,
                    content=json.dumps(result, ensure_ascii=False),
                    is_error=False,
                    execution_times=execution_time
                )
            else:
                error_result = {"error": result.get("error", "Unknown error")}
                return ToolRequestResult(
                    request=tool_request,
                    result=error_result,
                    content=json.dumps(error_result, ensure_ascii=False),
                    is_error=True,
                    execution_times=execution_time
                )

        except Exception as e:
            execution_time = time.time() - start_time
            error_result = {"error": str(e)}
            return ToolRequestResult(
                request=tool_request,
                result=error_result,
                content=json.dumps(error_result, ensure_ascii=False),
                is_error=True,
                execution_times=execution_time
            )

    def _search_with_grep_recursive(self, pattern: str, path: str,
                                    glob: Optional[str] = None,
                                    output_mode: str = "files_with_matches",
                                    context_before: Optional[int] = None,
                                    context_after: Optional[int] = None,
                                    context_around: Optional[int] = None,
                                    show_line_numbers: Optional[bool] = None,
                                    case_insensitive: Optional[bool] = None,
                                    use_extended_regexp: bool = False) -> Dict[str, Any]:
        """使用grep -r进行递归搜索（适用于目录）"""
        cmd = ['grep', '-r']
        
        # 扩展正则表达式
        if use_extended_regexp:
            cmd.append('-E')
        
        # 大小写不敏感
        if case_insensitive:
            cmd.append('-i')
        
        # 输出模式
        if output_mode == "count":
            cmd.append('-c')
        elif output_mode == "files_with_matches":
            cmd.append('-l')
        elif output_mode == "content":
            if show_line_numbers:
                cmd.append('-n')
            
            if context_around:
                cmd.extend(['-C', str(context_around)])
            else:
                if context_before:
                    cmd.extend(['-B', str(context_before)])
                if context_after:
                    cmd.extend(['-A', str(context_after)])
        
        # 添加常见的排除项
        exclude_patterns = [
            '--exclude-dir=.git',
            '--exclude-dir=node_modules',
            '--exclude-dir=__pycache__',
            '--exclude-dir=.venv',
            '--exclude-dir=venv',
            '--exclude-dir=.svn',
            '--exclude-dir=.hg',
            '--exclude=*.pyc',
            '--exclude=*.pyo',
            '--exclude=*.so',
            '--exclude=*.o',
            '--exclude=*.a',
            '--exclude=*.swp',
            '--exclude=.DS_Store'
        ]
        cmd.extend(exclude_patterns)
        
        # 如果有glob模式，添加include
        if glob:
            cmd.append(f'--include={glob}')
        
        # 添加pattern和路径
        cmd.append(pattern)
        cmd.append(path)
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=60, cwd=self.default_working_dir)
            
            return {
                'returncode': result.returncode,
                'stdout': result.stdout,
                'stderr': result.stderr,
                'command': ' '.join(cmd)
            }
        except subprocess.TimeoutExpired:
            return {
                'returncode': -1,
                'stdout': '',
                'stderr': 'Search timed out (60 seconds)',
                'command': ' '.join(cmd)
            }
        except Exception as e:
            return {
                'returncode': -1,
                'stdout': '',
                'stderr': str(e),
                'command': ' '.join(cmd)
            }

    def _search_batch_files(self, pattern: str, files: List[str],
                           output_mode: str = "files_with_matches",
                           context_before: Optional[int] = None,
                           context_after: Optional[int] = None,
                           context_around: Optional[int] = None,
                           show_line_numbers: Optional[bool] = None,
                           case_insensitive: Optional[bool] = None,
                           use_extended_regexp: bool = False) -> Dict[str, Any]:
        """分批搜索文件列表"""
        all_results = []
        
        # 分批处理文件
        for i in range(0, len(files), self.batch_size):
            batch = files[i:i + self.batch_size]
            
            cmd = ['grep']
            
            if use_extended_regexp:
                cmd.append('-E')
            
            if case_insensitive:
                cmd.append('-i')
            
            if output_mode == "count":
                cmd.append('-c')
            elif output_mode == "files_with_matches":
                cmd.append('-l')
            elif output_mode == "content":
                if show_line_numbers:
                    cmd.append('-n')
                
                if context_around:
                    cmd.extend(['-C', str(context_around)])
                else:
                    if context_before:
                        cmd.extend(['-B', str(context_before)])
                    if context_after:
                        cmd.extend(['-A', str(context_after)])
            
            # 添加显示文件名（多文件搜索时）
            if len(batch) > 1:
                cmd.append('-H')
            
            cmd.append(pattern)
            cmd.extend(batch)
            
            try:
                result = subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=30, cwd=self.default_working_dir)
                
                if result.returncode in [0, 1]:
                    all_results.append(result.stdout)
            except Exception:
                continue
        
        return {
            'returncode': 0 if all_results else 1,
            'stdout': '\n'.join(all_results),
            'stderr': '',
            'command': f'grep (batched over {len(files)} files)'
        }

    def _search(self, pattern: str, path: Optional[str] = None,
               glob: Optional[str] = None, output_mode: str = "files_with_matches",
               context_before: Optional[int] = None,
               context_after: Optional[int] = None,
               context_around: Optional[int] = None,
               show_line_numbers: Optional[bool] = None,
               case_insensitive: Optional[bool] = None,
               head_limit: Optional[int] = None,
               use_extended_regexp: bool = False) -> Dict[str, Any]:
        """使用原生grep搜索文件内容"""
        # 验证必需参数
        if not pattern:
            return {
                "success": False,
                "error": "pattern is required",
                "results": [],
                "count": 0
            }

        # 确定搜索路径
        search_path = path if path else self.default_working_dir
        abs_path = os.path.abspath(search_path)
        
        if not os.path.exists(abs_path):
            return {
                "success": False,
                "error": f"Search path does not exist: {search_path}",
                "results": [],
                "count": 0
            }

        # 如果是目录，使用grep -r递归搜索
        if os.path.isdir(abs_path):
            grep_result = self._search_with_grep_recursive(
                pattern, abs_path, glob, output_mode,
                context_before, context_after, context_around,
                show_line_numbers, case_insensitive, use_extended_regexp
            )
        else:
            # 单个文件，直接搜索
            grep_result = self._search_batch_files(
                pattern, [abs_path], output_mode,
                context_before, context_after, context_around,
                show_line_numbers, case_insensitive, use_extended_regexp
            )

        # 处理结果
        if grep_result['returncode'] == -1:
            return {
                "success": False,
                "error": grep_result['stderr'],
                "results": [],
                "count": 0
            }
        
        if grep_result['returncode'] in [0, 1]:
            # 解析输出
            if output_mode == "count":
                parsed_results = self._parse_count_output(grep_result['stdout'])
            elif output_mode == "files_with_matches":
                parsed_results = self._parse_files_output(grep_result['stdout'])
            else:  # content
                parsed_results = self._parse_content_output(grep_result['stdout'], show_line_numbers)

            # 应用head_limit
            if head_limit is not None and head_limit > 0:
                if isinstance(parsed_results, list):
                    parsed_results = parsed_results[:head_limit]
                elif isinstance(parsed_results, dict) and 'matches' in parsed_results:
                    parsed_results['matches'] = parsed_results['matches'][:head_limit]

            return {
                "success": True,
                "results": parsed_results,
                "count": len(parsed_results) if isinstance(parsed_results, list) else len(parsed_results.get('matches', [])),
                "output_mode": output_mode,
                "pattern": pattern,
                "search_path": abs_path,
                "command": grep_result['command']
            }
        else:
            return {
                "success": False,
                "error": f"grep error: {grep_result['stderr'] if grep_result['stderr'] else 'Unknown error'}",
                "results": [],
                "count": 0,
                "command": grep_result['command']
            }

    def _parse_count_output(self, stdout: str) -> List[Dict[str, Any]]:
        """解析count模式输出"""
        results = []
        for line in stdout.strip().split('\n'):
            if not line:
                continue
            if ':' in line:
                file_path, count = line.rsplit(':', 1)
                try:
                    count_num = int(count)
                    if count_num > 0:
                        results.append({
                            "file": file_path,
                            "count": count_num
                        })
                except ValueError:
                    continue
        return results

    def _parse_files_output(self, stdout: str) -> List[str]:
        """解析files_with_matches模式输出"""
        results = []
        for line in stdout.strip().split('\n'):
            if line.strip():
                results.append(line.strip())
        return results

    def _parse_content_output(self, stdout: str, show_line_numbers: Optional[bool]) -> Dict[str, Any]:
        """解析content模式输出"""
        matches = []
        
        for line in stdout.strip().split('\n'):
            if not line:
                continue

            # grep -n 格式: filename:line_number:content
            # grep 无-n格式: filename:content
            if ':' in line:
                parts = line.split(':', 2 if show_line_numbers else 1)
                
                if len(parts) >= 2:
                    file_path = parts[0]
                    
                    if show_line_numbers and len(parts) == 3:
                        try:
                            line_num = int(parts[1])
                            content = parts[2]
                            matches.append({
                                "file": file_path,
                                "line": line_num,
                                "content": content
                            })
                        except ValueError:
                            # 如果line_num不是数字，作为普通内容处理
                            matches.append({
                                "file": file_path,
                                "content": ':'.join(parts[1:])
                            })
                    else:
                        content = parts[1] if len(parts) > 1 else parts[0]
                        matches.append({
                            "file": file_path,
                            "content": content
                        })

        return {
            "matches": matches
        }
