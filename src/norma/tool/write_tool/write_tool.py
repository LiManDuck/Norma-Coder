"""
Write Tool - 继承自 Tool 基类的实现

将文件写入本地文件系统。

使用指南：
- 如果目标路径已存在文件，将会覆盖现有文件
- 如果是现有文件，必须先使用Read工具读取文件内容（安全检查）
- 优先编辑代码库中的现有文件，除非明确要求否则不要创建新文件
- 不要主动创建文档文件（*.md）或README文件，仅在用户明确要求时创建
- 仅在用户明确要求时使用emoji，避免向文件写入emoji

重要规则：
- 文件路径必须是绝对路径，不能是相对路径
- 内容参数是必需的，包含要写入文件的完整内容
- 工具会完全覆盖文件的现有内容
- 用于创建新文件或完全替换现有文件内容
- 遵循"编辑优于新建"的原则
- 小心处理重要文件，避免意外覆盖

关键注意事项：
- 必须读取现有文件才能覆盖（安全功能）
- 优先编辑现有文件，减少不必要的文件创建
- 绝对路径要求确保文件位置的准确性
- 避免创建非请求的文档文件
- 写入前请确认内容的完整性和正确性
"""

import os
import tempfile
import shutil
import json
import time
import hashlib
from typing import Dict, Any, Optional
from pathlib import Path

from norma.core.tool_types import (
    Tool,
    ToolSchema,
    ParametersSchema,
    ToolRequest,
    ToolRequestResult,
    ToolRequestError
)


class WriteTool(Tool):
    """Write工具类 - 继承自Tool基类,用于写入文件"""

    def __init__(self, read_files_registry: Optional[set] = None):
        """初始化WriteTool

        Args:
            read_files_registry: 与 Read/Edit 共享的已读文件集合；Write 成功后
                会把绝对路径加入其中，使后续 Edit 通过「先读后编」校验。
        """
        self.read_files_registry = read_files_registry if read_files_registry is not None else set()
        self.backup_enabled = True


    

    @property
    def name(self) -> str:
        return "Write"

    @property
    def description(self) -> str:
        return """
将文件写入本地文件系统。

使用指南：
- 如果目标路径已存在文件，将会覆盖现有文件
- 如果是现有文件，必须先使用Read工具读取文件内容（安全检查）
- 优先编辑代码库中的现有文件，除非明确要求否则不要创建新文件
- 不要主动创建文档文件（*.md）或README文件，仅在用户明确要求时创建
- 仅在用户明确要求时使用emoji，避免向文件写入emoji

重要规则：
- 文件路径必须是绝对路径，不能是相对路径
- 内容参数是必需的，包含要写入文件的完整内容
- 工具会完全覆盖文件的现有内容
- 用于创建新文件或完全替换现有文件内容
- 遵循"编辑优于新建"的原则
- 小心处理重要文件，避免意外覆盖

关键注意事项：
- 必须读取现有文件才能覆盖（安全功能）
- 优先编辑现有文件，减少不必要的文件创建
- 绝对路径要求确保文件位置的准确性
- 避免创建非请求的文档文件
- 写入前请确认内容的完整性和正确性
        """

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=ParametersSchema(
                type="object",
                properties={
                    "file_path": {
                        "type": "string",
                        "description": "要写入文件的绝对路径（必需，必须是绝对路径）"
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入文件的内容（必需）"
                    }
                },
                required=["file_path", "content"]
            ),
            strict=False
        )


    def mark_file_as_read(self, file_path: str):
        """标记文件为已读取（由Read工具调用）"""
        abs_path = os.path.abspath(file_path)
        self.read_files_registry.add(abs_path)

    async def execute(self, tool_request: ToolRequest) -> ToolRequestResult:
        """执行Write工具"""
        start_time = time.time()

        # 从ToolRequest中提取参数
        if isinstance(tool_request.tool_call_arguments, str):
            call_args = self.parse_string_arguments(tool_request.tool_call_arguments)
        else:
            call_args = tool_request.tool_call_arguments

        # 验证必需参数
        required_params = ["file_path", "content"]
        for param in required_params:
            if param not in call_args:
                execution_time = time.time() - start_time
                error_result = {"error": f"Missing required parameter: {param}"}
                return ToolRequestResult(
                    request=tool_request,
                    result=error_result,
                    content=json.dumps(error_result, ensure_ascii=False),
                    is_error=True,
                    execution_times=execution_time
                )

        # 执行写入操作
        try:
            return self._write_file(
                file_path=call_args["file_path"],
                content=call_args["content"],
                tool_request=tool_request
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
    def _write_file(self, file_path: str, content: str, tool_request: ToolRequest) -> ToolRequestResult:
        """写入文件内容到本地文件系统"""
        start_time = time.time()
        
        # 验证必需参数
        if not file_path:
            execution_time = time.time() - start_time
            error_result = {
                "error": "file_path is required",
                "path": file_path,
                "bytes_written": 0
            }
            return ToolRequestResult(
                request=tool_request,
                result=error_result,
                content=json.dumps(error_result, ensure_ascii=False),
                is_error=True,
                execution_times=execution_time
            )

        if not isinstance(content, str):
            execution_time = time.time() - start_time
            error_result = {
                "error": "content must be a string",
                "path": file_path,
                "bytes_written": 0
            }
            return ToolRequestResult(
                request=tool_request,
                result=error_result,
                content=json.dumps(error_result, ensure_ascii=False),
                is_error=True,
                execution_times=execution_time
            )

        # 规范化为绝对路径
        abs_path = os.path.abspath(file_path)

        # 检查文件是否存在
        file_exists = os.path.exists(abs_path)
        file_was_file = False
        file_hash_before = None

        if file_exists:
            file_was_file = os.path.isfile(abs_path)

            # 安全检查：如果文件存在，检查是否已经读取过
            if file_was_file and abs_path not in self.read_files_registry:
                execution_time = time.time() - start_time
                error_result = {
                    "error": "You must use the Read tool first before overwriting an existing file. This is a safety requirement.",
                    "path": abs_path,
                    "existing_file": True,
                    "suggestion": f"Use Read tool on {abs_path} first"
                }
                return ToolRequestResult(
                    request=tool_request,
                    result=error_result,
                    content=json.dumps(error_result, ensure_ascii=False),
                    is_error=True,
                    execution_times=execution_time
                )

            # 获取文件哈希（用于验证写入）
            try:
                with open(abs_path, 'rb') as f:
                    file_hash_before = hashlib.md5(f.read()).hexdigest()
            except:
                file_hash_before = None

        try:
            # 创建目录（如果不存在）
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)

            
            
            # 原子写入（通过临时文件）
            bytes_written, file_hash_after = self._atomic_write(abs_path, content)

            # 标记为已读取（允许后续编辑）
            self.mark_file_as_read(abs_path)

            # 计算哈希差异
            hash_changed = file_hash_before != file_hash_after

            execution_time = time.time() - start_time
            
            success_result = {
                "success": True,
                "path": abs_path,
                "bytes_written": bytes_written,
                "existed_before": file_exists,
                "was_file_before": file_was_file,
           #     "backup_created": backup_info is not None,
            #    "backup_info": backup_info,
                "content_hash": file_hash_after,
                "previous_hash": file_hash_before,
                "content_changed": hash_changed,
                "timestamp": time.time()
            }
            
            return ToolRequestResult(
                request=tool_request,
                result=success_result,
                content=json.dumps(success_result, ensure_ascii=False),
                is_error=False,
                execution_times=execution_time
            )

        except PermissionError:
            execution_time = time.time() - start_time
            error_result = {
                "error": f"Permission denied when writing to: {abs_path}",
                "path": abs_path,
                "bytes_written": 0
            }
            return ToolRequestResult(
                request=tool_request,
                result=error_result,
                content=json.dumps(error_result, ensure_ascii=False),
                is_error=True,
                execution_times=execution_time
            )
            
        except OSError as e:
            execution_time = time.time() - start_time
            error_result = {
                "error": f"OS error when writing file: {str(e)}",
                "path": abs_path,
                "bytes_written": 0
            }
            return ToolRequestResult(
                request=tool_request,
                result=error_result,
                content=json.dumps(error_result, ensure_ascii=False),
                is_error=True,
                execution_times=execution_time
            )
            
        except Exception as e:
            execution_time = time.time() - start_time
            error_result = {
                "error": f"Error writing file: {str(e)}",
                "path": abs_path,
                "bytes_written": 0
            }
            return ToolRequestResult(
                request=tool_request,
                result=error_result,
                content=json.dumps(error_result, ensure_ascii=False),
                is_error=True,
                execution_times=execution_time
            )
    def _atomic_write(self, file_path: str, content: str) -> tuple[int, str]:
        """原子写入文件内容"""
        # 创建临时文件
        temp_fd, temp_path = tempfile.mkstemp(
            dir=os.path.dirname(file_path),
            prefix='.tmp_write_'
        )

        try:
            # 写入临时文件
            with os.fdopen(temp_fd, 'w', encoding='utf-8') as temp_file:
                temp_file.write(content)
                temp_file.flush()
                os.fsync(temp_fd)  # 强制写入磁盘

            # 计算内容哈希
            content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()

            # 原子移动到目标位置
            shutil.move(temp_path, file_path)

            bytes_written = len(content.encode('utf-8'))
            return bytes_written, content_hash

        except Exception:
            # 清理临时文件
            try:
                os.unlink(temp_path)
            except:
                pass
            raise

    def _create_backup(self, file_path: str) -> Optional[Dict[str, Any]]:
        """创建文件备份"""
        try:
            backup_dir = tempfile.mkdtemp(prefix='write_tool_backup_')
            timestamp = int(time.time())
            backup_name = f"{os.path.basename(file_path)}.backup_{timestamp}"
            backup_path = os.path.join(backup_dir, backup_name)

            # 复制文件到备份位置
            shutil.copy2(file_path, backup_path)

            return {
                "backup_path": backup_path,
                "original_path": file_path,
                "timestamp": timestamp,
                "size": os.path.getsize(backup_path)
            }

        except Exception:
            return None

    def can_write_file(self, file_path: str) -> Dict[str, Any]:
        """检查文件是否可写"""
        try:
            abs_path = os.path.abspath(file_path)

            # 检查父目录是否可写
            parent_dir = os.path.dirname(abs_path)
            if not os.path.exists(parent_dir):
                return {
                    "can_write": False,
                    "reason": "Parent directory does not exist"
                }

            if not os.access(parent_dir, os.W_OK):
                return {
                    "can_write": False,
                    "reason": "Parent directory is not writable"
                }

            # 如果文件存在，检查是否可写
            if os.path.exists(abs_path):
                if not os.access(abs_path, os.W_OK):
                    return {
                        "can_write": False,
                        "reason": "File is not writable"
                    }

                # 检查是否已经读取过
                if abs_path in self.read_files_registry:
                    return {
                        "can_write": True,
                        "reason": "File exists and has been read",
                        "safety_check": "passed"
                    }
                else:
                    return {
                        "can_write": False,
                        "reason": "File exists but has not been read first",
                        "safety_check": "failed",
                        "suggestion": "Use Read tool first"
                    }
            else:
                return {
                    "can_write": True,
                    "reason": "File does not exist, can be created",
                    "safety_check": "not_required"
                }

        except Exception as e:
            return {
                "can_write": False,
                "reason": f"Error checking write permission: {str(e)}"
            }
