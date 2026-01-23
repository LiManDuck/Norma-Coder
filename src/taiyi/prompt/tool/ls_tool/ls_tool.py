"""
Ls Tool - 继承自 Tool 基类的实现

列出指定路径下的文件和目录。path 参数必须是绝对路径，而不是相对路径。你可以通过 ignore 参数选择性地提供一个 glob 模式数组来忽略某些文件。如果你知道要搜索哪些目录，通常应该优先使用 Glob 和 Grep 工具。

使用指南：
- 路径必须是绝对路径，不能是相对路径
- 可选的 ignore 参数接受 glob 模式数组
- 用于探索目录结构和了解文件组织
- 当有明确搜索目标时，考虑使用 Glob 或 Grep 工具
- 可以帮助在使用其他工具前识别可用的文件
- 支持使用 ignore 模式过滤不需要的文件/目录

关键注意事项：
- 始终为 path 参数使用绝对路径
- ignore 模式使用标准的 glob 语法
- 返回文件和目录的列表
- 有助于理解代码库结构
- 可用于在执行其他操作前验证文件是否存在
"""

import os
import fnmatch
import json
import time
from typing import Optional, Dict, Any, List
from pathlib import Path
import stat

from taiyi.core.tool_types import (
    Tool,
    ToolSchema,
    ParametersSchema,
    ToolRequest,
    ToolRequestResult,
    ToolRequestError
)


class LsTool(Tool):
    """Ls工具类 - 继承自Tool基类,用于列出目录内容"""

    def __init__(self,
        cwd: str | Path |None = None
        
    ) -> None:
        
        self.cwd = str(cwd)


    @property
    def name(self) -> str:
        return "Ls"

    @property
    def description(self) -> str:
        return """
列出指定路径下的文件和目录。path 参数必须是绝对路径，而不是相对路径。你可以通过 ignore 参数选择性地提供一个 glob 模式数组来忽略某些文件。如果你知道要搜索哪些目录，通常应该优先使用 Glob 和 Grep 工具。

使用指南：
- 路径必须是绝对路径，不能是相对路径
- 可选的 ignore 参数接受 glob 模式数组
- 用于探索目录结构和了解文件组织
- 当有明确搜索目标时，考虑使用 Glob 或 Grep 工具
- 可以帮助在使用其他工具前识别可用的文件
- 支持使用 ignore 模式过滤不需要的文件/目录

关键注意事项：
- 始终为 path 参数使用绝对路径
- ignore 模式使用标准的 glob 语法
- 返回文件和目录的列表
- 有助于理解代码库结构
- 可用于在执行其他操作前验证文件是否存在
        """

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=ParametersSchema(
                type="object",
                properties={
                    "path": {
                        "type": "string",
                        "description": "要列出目录的绝对路径（必须是绝对路径，不能是相对路径）"
                    },
                    "ignore": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "要忽略的glob模式数组（可选）"
                    }
                },
                required=["path"]
            ),
            strict=False
        )

    async def execute(self, tool_request: ToolRequest) -> ToolRequestResult:
        """执行Ls工具"""
        start_time = time.time()

        # 从ToolRequest中提取参数
        if isinstance(tool_request.tool_call_arguments, str):
            call_args = self.parse_string_arguments(tool_request.tool_call_arguments)
        else:
            call_args = tool_request.tool_call_arguments

        # 验证必需参数
        if "path" not in call_args:
            execution_time = time.time() - start_time
            error_result = {"error": "Missing required parameter: path"}
            return ToolRequestResult(
                request=tool_request,
                result=error_result,
                content=json.dumps(error_result, ensure_ascii=False),
                is_error=True,
                execution_times=execution_time
            )

        # 执行目录列出操作
        try:
            result = self._list_directory(
                path=call_args["path"],
                ignore=call_args.get("ignore")
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

    def _list_directory(self, path: str, ignore: Optional[List[str]] = None) -> Dict[str, Any]:
        """列出目录中的文件和子目录"""
        # 验证必需参数
        if not path:
            return {
                "success": False,
                "error": "path is required",
                "path": path,
                "items": [],
                "count": 0
            }

        # 规范化为绝对路径
        abs_path = os.path.abspath(path)

        # 检查路径是否存在
        if not os.path.exists(abs_path):
            return {
                "success": False,
                "error": f"Path does not exist: {abs_path}",
                "path": abs_path,
                "items": [],
                "count": 0
            }

        # 检查是否为目录
        if not os.path.isdir(abs_path):
            return {
                "success": False,
                "error": f"Path is not a directory: {abs_path}",
                "path": abs_path,
                "items": [],
                "count": 0
            }

        try:
            # 获取目录中的所有条目
            all_entries = os.listdir(abs_path)

            # 应用忽略过滤
            filtered_entries = self._apply_ignore_filter(all_entries, ignore)

            # 获取每个条目的详细信息
            items = []
            for entry in filtered_entries:
                entry_path = os.path.join(abs_path, entry)
                item_info = self._get_entry_info(entry_path, entry)
                if item_info:
                    items.append(item_info)

            # 排序：目录在前，文件在后；按名称排序
            items.sort(key=lambda x: (not x["is_directory"], x["name"].lower()))

            return {
                "success": True,
                "path": abs_path,
                "items": items,
                "count": len(items),
                "total_entries": len(all_entries),
                "filtered_count": len(all_entries) - len(items),
                "ignore_patterns": ignore or []
            }

        except PermissionError:
            return {
                "success": False,
                "error": f"Permission denied accessing directory: {abs_path}",
                "path": abs_path,
                "items": [],
                "count": 0
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Error listing directory: {str(e)}",
                "path": abs_path,
                "items": [],
                "count": 0
            }

    def _apply_ignore_filter(self, entries: List[str], ignore_patterns: Optional[List[str]]) -> List[str]:
        """应用忽略模式过滤条目"""
        if not ignore_patterns:
            return entries

        filtered = []
        for entry in entries:
            should_ignore = False
            for pattern in ignore_patterns:
                if fnmatch.fnmatch(entry, pattern):
                    should_ignore = True
                    break
            if not should_ignore:
                filtered.append(entry)

        return filtered

    def _get_entry_info(self, entry_path: str, entry_name: str) -> Optional[Dict[str, Any]]:
        """获取文件或目录的详细信息"""
        try:
            stat_info = os.stat(entry_path, follow_symlinks=False)

            item_info = {
                "name": entry_name,
                "path": entry_path,
                "size": stat_info.st_size,
                "modified_time": stat_info.st_mtime,
                "is_directory": stat.S_ISDIR(stat_info.st_mode),
                "is_file": stat.S_ISREG(stat_info.st_mode),
                "is_symlink": stat.S_ISLNK(stat_info.st_mode),
                "permissions": oct(stat_info.st_mode)[-3:],
            }

            # 如果是符号链接，获取链接目标
            if item_info["is_symlink"]:
                try:
                    link_target = os.readlink(entry_path)
                    item_info["link_target"] = link_target
                    # 检查链接目标是否存在
                    if os.path.exists(link_target):
                        target_stat = os.stat(link_target)
                        item_info["target_is_directory"] = stat.S_ISDIR(target_stat.st_mode)
                    else:
                        item_info["target_is_directory"] = False
                        item_info["target_broken"] = True
                except:
                    item_info["link_target"] = "broken link"
                    item_info["target_broken"] = True

            # 如果是文件，尝试获取文件类型信息
            if item_info["is_file"]:
                file_ext = Path(entry_name).suffix.lower()
                item_info["extension"] = file_ext
                item_info["file_type"] = self._get_file_type(file_ext)

            # 添加可读性信息
            item_info["readable"] = os.access(entry_path, os.R_OK)
            item_info["writable"] = os.access(entry_path, os.W_OK)
            item_info["executable"] = os.access(entry_path, os.X_OK)

            return item_info

        except (OSError, PermissionError):
            # 如果无法获取文件状态，返回基本信息
            return {
                "name": entry_name,
                "path": entry_path,
                "error": "Cannot access entry"
            }

    def _get_file_type(self, extension: str) -> str:
        """根据文件扩展名推断文件类型"""
        ext_type_map = {
            '.py': 'python',
            '.js': 'javascript',
            '.ts': 'typescript',
            '.jsx': 'react',
            '.tsx': 'react_typescript',
            '.java': 'java',
            '.cpp': 'cpp',
            '.c': 'c',
            '.h': 'c_header',
            '.hpp': 'cpp_header',
            '.rs': 'rust',
            '.go': 'go',
            '.php': 'php',
            '.rb': 'ruby',
            '.swift': 'swift',
            '.kt': 'kotlin',
            '.scala': 'scala',
            '.cs': 'csharp',
            '.html': 'html',
            '.htm': 'html',
            '.css': 'css',
            '.scss': 'scss',
            '.sass': 'sass',
            '.less': 'less',
            '.json': 'json',
            '.xml': 'xml',
            '.yaml': 'yaml',
            '.yml': 'yaml',
            '.toml': 'toml',
            '.ini': 'ini',
            '.conf': 'config',
            '.md': 'markdown',
            '.txt': 'text',
            '.log': 'log',
            '.pdf': 'pdf',
            '.png': 'png',
            '.jpg': 'jpg',
            '.jpeg': 'jpg',
            '.gif': 'gif',
            '.svg': 'svg',
            '.zip': 'zip',
            '.tar': 'tar',
            '.gz': 'gzip',
            '.sql': 'sql',
            '.sh': 'shell',
            '.bat': 'batch',
            '.dockerfile': 'dockerfile',
        }
        return ext_type_map.get(extension, 'unknown')
