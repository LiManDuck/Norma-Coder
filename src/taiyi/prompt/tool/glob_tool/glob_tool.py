"""
Glob Tool - 继承自 Tool 基类的实现

快速文件模式匹配工具，适用于任何代码库大小。支持通配符模式如"**/*.js"或"src/**/*.ts"。返回按修改时间排序的匹配文件路径。
"""

import os
import glob
import fnmatch
import json
import time
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path

from taiyi.core.tool_types import (
    Tool,
    ToolSchema,
    ParametersSchema,
    ToolRequest,
    ToolRequestResult,
    ToolRequestError
)


class GlobTool(Tool):
    """Glob工具类 - 继承自Tool基类,用于文件模式匹配"""

    def __init__(self,
        cwd: str | Path | None = None, 
        max_files_show: int = 1000,
    ) -> None:
        self.max_results = max_files_show
        # 确保 cwd 转换为绝对路径
        self.cwd = str(Path(cwd).resolve()) if cwd else os.getcwd()

    @property
    def name(self) -> str:
        return "Glob"

    @property
    def description(self) -> str:
        return """
快速文件模式匹配工具，适用于任何代码库大小。支持通配符模式如"**/*.js"或"src/**/*.ts"。返回按修改时间排序的匹配文件路径。

使用指南：
- 支持标准 glob 模式：通配符(*)、递归模式(**)和字符类([])
- 结果自动按修改时间排序（最新优先）
- 适合通过扩展名、名称模式或目录结构查找文件
- pattern 参数必需，path 参数可选（默认使用初始化时的 cwd）

关键注意事项：
- 模式参数是必需的，不能为空
- 路径参数可选，默认使用工具初始化时的工作目录
- 只返回文件，不包括目录
- 支持递归和非递归模式
- 默认限制最多返回配置的最大结果数
- 建议与其他工具一起批量使用以提高效率
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
                        "description": "要匹配的glob模式（必需），支持标准glob语法如 **/*.py 或 src/**/*.js"
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索目录（可选，默认使用工具初始化时的工作目录，建议使用绝对路径）"
                    }
                },
                required=["pattern"]
            ),
            strict=False
        )

    async def execute(self, tool_request: ToolRequest) -> ToolRequestResult:
        """执行Glob工具"""
        start_time = time.time()

        # 从ToolRequest中提取参数
        if isinstance(tool_request.tool_call_arguments, str):
            try:
                call_args = self.parse_string_arguments(tool_request.tool_call_arguments)
            except Exception as e:
                return ToolRequestResult(
                    request=tool_request,
                    result=e,
                    is_error=False,
                    content=f'{str(e)}'
                )
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

        # 获取 path 参数，如果没有提供则使用 self.cwd
        path = call_args.get("path", self.cwd)

        # 直接调用并返回结果
        return self._find_files(
            tool_request=tool_request,
            pattern=call_args["pattern"],
            path=path
        )

    def _find_files(
        self, 
        tool_request: ToolRequest,
        pattern: str, 
        path: str
    ) -> ToolRequestResult:
        """使用glob模式查找文件，直接返回 ToolRequestResult"""
        start_time = time.time()
        
        # 参数验证
        if not pattern:
            execution_time = time.time() - start_time
            error_result = {
                "success": False,
                "error": "pattern cannot be empty",
                "files": [],
                "count": 0
            }
            return ToolRequestResult(
                request=tool_request,
                result=error_result,
                content=json.dumps(error_result, ensure_ascii=False),
                is_error=True,
                execution_times=execution_time
            )

        # 确定搜索路径 - 如果是相对路径，转换为绝对路径
        if os.path.isabs(path):
            search_path = path
        else:
            # 相对路径基于 self.cwd 解析
            search_path = os.path.abspath(os.path.join(self.cwd, path))

        # 验证搜索路径
        if not os.path.exists(search_path):
            execution_time = time.time() - start_time
            error_result = {
                "success": False,
                "error": f"Search directory does not exist: {search_path}",
                "files": [],
                "count": 0,
                "cwd_used": self.cwd
            }
            return ToolRequestResult(
                request=tool_request,
                result=error_result,
                content=json.dumps(error_result, ensure_ascii=False),
                is_error=True,
                execution_times=execution_time
            )

        if not os.path.isdir(search_path):
            execution_time = time.time() - start_time
            error_result = {
                "success": False,
                "error": f"Search path is not a directory: {search_path}",
                "files": [],
                "count": 0
            }
            return ToolRequestResult(
                request=tool_request,
                result=error_result,
                content=json.dumps(error_result, ensure_ascii=False),
                is_error=True,
                execution_times=execution_time
            )

        # 执行文件搜索
        try:
            files = self._perform_glob_search(pattern, search_path)

            # 未找到文件的情况
            if not files:
                execution_time = time.time() - start_time
                suggestions = self._get_pattern_suggestions(pattern)
                result = {
                    "success": True,
                    "files": [],
                    "count": 0,
                    "search_path": search_path,
                    "pattern": pattern,
                    "suggestions": suggestions
                }
                return ToolRequestResult(
                    request=tool_request,
                    result=result,
                    content=json.dumps(result, ensure_ascii=False),
                    is_error=False,
                    execution_times=execution_time
                )

            # 按修改时间排序
            sorted_files = self._sort_files_by_mtime(files)

            # 限制结果数量
            truncated = len(sorted_files) > self.max_results
            if truncated:
                sorted_files = sorted_files[:self.max_results]

            # 获取统计信息
            stats = self._get_file_stats(sorted_files)

            execution_time = time.time() - start_time
            
            result = {
                "success": True,
                "files": sorted_files,
                "count": len(sorted_files),
                "search_path": search_path,
                "pattern": pattern,
                "truncated": truncated,
                "max_results": self.max_results,
                "stats": stats
            }
            
            return ToolRequestResult(
                request=tool_request,
                result=result,
                content=json.dumps(result, ensure_ascii=False),
                is_error=False,
                execution_times=execution_time
            )

        except Exception as e:
            execution_time = time.time() - start_time
            error_result = {
                "success": False,
                "error": f"Error during file search: {str(e)}",
                "files": [],
                "count": 0
            }
            return ToolRequestResult(
                request=tool_request,
                result=error_result,
                content=json.dumps(error_result, ensure_ascii=False),
                is_error=True,
                execution_times=execution_time
            )

    def _perform_glob_search(self, pattern: str, search_path: str) -> List[str]:
        """执行实际的glob搜索"""
        # 组合搜索路径和模式
        if os.path.isabs(pattern):
            # 绝对路径模式
            search_pattern = pattern
        else:
            # 相对路径模式，需要与搜索路径组合
            search_pattern = os.path.join(search_path, pattern)

        # 使用glob进行搜索，支持递归模式
        # 将反斜杠转换为正斜杠以确保跨平台兼容性
        search_pattern = search_pattern.replace('\\', '/')

        # 执行glob搜索
        if '**' in pattern:
            # 对于递归模式，使用glob.iglob
            files = list(glob.iglob(search_pattern, recursive=True))
        else:
            # 对于非递归模式，使用glob.glob
            files = list(glob.glob(search_pattern))

        # 转换为绝对路径并过滤文件
        absolute_files = []
        for file_path in files:
            file_path = os.path.abspath(file_path)
            if os.path.isfile(file_path):
                absolute_files.append(file_path)

        return absolute_files

    def _sort_files_by_mtime(self, files: List[str]) -> List[str]:
        """按修改时间排序文件（最新优先）"""
        def get_mtime(file_path):
            try:
                return os.path.getmtime(file_path)
            except:
                return 0

        return sorted(files, key=get_mtime, reverse=True)

    def _get_file_stats(self, files: List[str]) -> Dict[str, Any]:
        """获取文件统计信息"""
        if not files:
            return {}

        total_size = 0
        extensions = {}
        oldest_time = float('inf')
        newest_time = 0

        for file_path in files:
            try:
                stat = os.stat(file_path)
                total_size += stat.st_size
                oldest_time = min(oldest_time, stat.st_mtime)
                newest_time = max(newest_time, stat.st_mtime)

                # 统计文件扩展名
                ext = Path(file_path).suffix.lower()
                extensions[ext] = extensions.get(ext, 0) + 1
            except:
                continue

        return {
            "total_size": total_size,
            "size_mb": round(total_size / (1024 * 1024), 2),
            "extensions": extensions,
            "oldest_file_time": oldest_time if oldest_time != float('inf') else None,
            "newest_file_time": newest_time,
            "oldest_file_date": time.ctime(oldest_time) if oldest_time != float('inf') else None,
            "newest_file_date": time.ctime(newest_time) if newest_time else None
        }

    def _get_pattern_suggestions(self, pattern: str) -> List[str]:
        """获取模式建议"""
        suggestions = []

        # 建议使用递归模式
        if '**' not in pattern and '/' not in pattern:
            suggestions.append(f"尝试递归模式: **/{pattern}")

        # 建议添加文件扩展名
        common_exts = ['.py', '.js', '.ts', '.java', '.cpp', '.c', '.html', '.css']
        if not any(ext in pattern.lower() for ext in common_exts):
            suggestions.append("添加文件扩展名，如 *.py 或 **/*.js")

        # 检查路径分隔符
        if '\\' in pattern:
            suggestions.append("使用正斜杠 (/) 替代反斜杠 (\\) 以提高兼容性")

        return suggestions
