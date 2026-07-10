"""
Edit Tool - 继承自 Tool 基类的实现

在文件中执行精确的字符串替换。

使用规则：
- 编辑文件前必须先读取文件（如果未读取 会拒绝执行编辑操作）
- 当编辑从Read工具输出的文本时，确保保持精确的缩进（制表符/空格），即行号前缀之后的内容
- 行号前缀格式为：空格 + 行号 + 制表符。制表符后的所有内容才是需要匹配的实际文件内容
- 不要在old_string或new_string中包含任何行号前缀
- 除非用户明确要求，否则不要使用表情符号
- 如果old_string在文件中不唯一，编辑将失败。提供更大的字符串和更多上下文使其唯一，或使用replace_all更改每个实例
- 使用replace_all替换和重命名文件中的字符串，例如重命名变量

关键注意事项：
- 始终先读取文件以了解其内容和结构
- 保持精确的格式、缩进和行尾
- 确保old_string与文件中的内容完全匹配
- 当要更改所有出现的内容时，使用replace_all=True
- 对于变量重命名，replace_all特别有用
- 如果编辑失败，检查old_string是否唯一且完全匹配
"""

import os
import difflib
import tempfile
import json
import time
from typing import Set

from typing import Optional, Dict, Any, Tuple
from pathlib import Path

from norma.core.tool_types import (
    Tool,
    ToolSchema,
    ParametersSchema,
    ToolRequest,
    ToolRequestResult,
    ToolRequestError
)


class EditTool(Tool):
    """Edit工具类 - 继承自Tool基类,用于文件编辑"""

    @property
    def name(self) -> str:
        return "Edit"

    @property
    def description(self) -> str:
        return """
在文件中执行精确的字符串替换。

使用规则：
- 编辑文件前必须先读取文件（本工具会强制执行此要求）
- 必须使用绝对路径, 如果使用相对路径则默认为当前的目录下
- 当编辑从Read工具输出的文本时，确保保持精确的缩进（制表符/空格），即行号前缀之后的内容
- 行号前缀格式为：空格 + 行号 + 制表符。制表符后的所有内容才是需要匹配的实际文件内容
- 不要在old_string或new_string中包含任何行号前缀
- 优先编辑代码库中的现有文件，除非明确要求，否则不要写新文件
- 除非用户明确要求，否则不要使用表情符号
- 如果old_string在文件中不唯一，编辑将失败。提供更大的字符串和更多上下文使其唯一，或使用replace_all更改每个实例
- 使用replace_all替换和重命名文件中的字符串，例如重命名变量

关键注意事项：
- 始终先读取文件以了解其内容和结构
- 保持精确的格式、缩进和行尾
- 确保old_string与文件中的内容完全匹配
- 当要更改所有出现的内容时，使用replace_all=True
- 对于变量重命名，replace_all特别有用
- 如果编辑失败，检查old_string是否唯一且完全匹配
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
                        "description": "要修改文件的绝对路径（必需）"
                    },
                    "old_string": {
                        "type": "string",
                        "description": "要替换的文本内容（必需）"
                    },
                    "new_string": {
                        "type": "string",
                        "description": "替换后的新文本内容（必需，必须与old_string不同）"
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "是否替换文件中所有出现的old_string（默认false）"
                    }
                },
                required=["file_path", "old_string", "new_string"]
            ),
            strict=False
        )

    def __init__(self , 
        readed_files: Optional[Set[str]] = None,
        cwd: Optional[str | Path] = None ,
        allow_edit_files : list[str] | None = None,


        ):
        """初始化EditTool"""
        self.readed_files = readed_files if readed_files is not None else set()  # 跟踪已读取的文件 , 实际环境中会有一个共享变量来初始化

        self.cwd = Path(cwd) if cwd else Path.cwd()


    def is_file_read(self, file_path: str) -> bool:
        """检查文件是否已被读取

        Args:
            file_path: 文件路径（可以是相对路径或绝对路径）

        Returns:
            如果文件已被读取返回True，否则返回False
        """
        path = Path(file_path)
        if not path.is_absolute():
            path = self.cwd / path

        abs_path = str(path.resolve())
        return abs_path in self.readed_files



    async def execute(self, tool_request: ToolRequest) -> ToolRequestResult:
        """执行Edit工具"""
        start_time = time.time()
        # 从ToolRequest中提取参数
        if isinstance(tool_request.tool_call_arguments, str):
            try:

                call_args = self.parse_string_arguments(tool_request.tool_call_arguments)
            
            except ToolRequestError as e:
                execution_time = time.time() - start_time
                return ToolRequestResult(
                    request=tool_request,
                    result=e,
                    is_error=True,
                    content=f'{str(e)}',
                    execution_times=execution_time
                )
        else:
            call_args = tool_request.tool_call_arguments




        # 验证必需参数
        required_params = ["file_path", "old_string", "new_string"]
        for param in required_params:
            if param not in call_args:
                execution_time = time.time() - start_time
                error_result = ToolRequestError(
                     f"Missing required parameter: {param}"
                )


                return ToolRequestResult(
                    request=tool_request,
                    result=error_result,
                    content= str(error_result),
                    is_error=True,
                    execution_times=execution_time
                )

        # 执行编辑操作
        try:
            result = self._edit_file(
                tool_request=tool_request,
                file_path=call_args["file_path"],
                old_string=call_args["old_string"],
                new_string=call_args["new_string"],
                replace_all=call_args.get("replace_all", False)
            )
            return result
        except Exception as e:
            execution_time = time.time() - start_time
            error_result = ToolRequestError(str(e))
            return ToolRequestResult(
                request=tool_request,
                result=error_result,
                content=str(error_result),
                is_error=True,
                execution_times=execution_time
            )
    def _edit_file(
        self,
        tool_request: ToolRequest,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False
    ) -> ToolRequestResult:
        """执行文件编辑操作
        
        Args:
            tool_request: 工具请求对象
            file_path: 文件路径
            old_string: 要替换的旧字符串
            new_string: 替换后的新字符串
            replace_all: 是否替换所有出现
            
        Returns:
            ToolRequestResult对象
        """
        start_time = time.time()
        
        # 转换为绝对路径
        path = Path(file_path)
        if not path.is_absolute():
            path = self.cwd / path
        abs_file_path = str(path.resolve())

        # 检查文件是否已被读取
        if not self.is_file_read(file_path):
            execution_time = time.time() - start_time
            error_msg = f"You must read the file {file_path} before editing it. Please use the Read tool first."
            result_dict = {
                "success": False,
                "error": error_msg,
                "changes_made": 0
            }
            return ToolRequestResult(
                request=tool_request,
                result=result_dict,
                content=json.dumps(result_dict, ensure_ascii=False),
                is_error=True,
                execution_times=execution_time
            )

        try:
            # 读取原文件内容
            with open(abs_file_path, 'r', encoding='utf-8') as f:
                original_content = f.read()

            # 检查old_string是否在文件中存在
            if old_string not in original_content:
                execution_time = time.time() - start_time
                result_dict = {
                    "success": False,
                    "error": "The old_string was not found in the file. Please check that the text matches exactly.",
                    "changes_made": 0
                }
                return ToolRequestResult(
                    request=tool_request,
                    result=result_dict,
                    content=json.dumps(result_dict, ensure_ascii=False),
                    is_error=True,
                    execution_times=execution_time
                )

            # 检查old_string的唯一性（当replace_all=False时）
            if not replace_all:
                occurrences = original_content.count(old_string)
                if occurrences > 1:
                    execution_time = time.time() - start_time
                    result_dict = {
                        "success": False,
                        "error": f"The old_string appears {occurrences} times in the file. Please provide more context to make it unique, or use replace_all=True.",
                        "changes_made": 0
                    }
                    return ToolRequestResult(
                        request=tool_request,
                        result=result_dict,
                        content=json.dumps(result_dict, ensure_ascii=False),
                        is_error=True,
                        execution_times=execution_time
                    )

            # 执行替换
            if replace_all:
                new_content = original_content.replace(old_string, new_string)
                changes_made = original_content.count(old_string)
            else:
                new_content = original_content.replace(old_string, new_string, 1)
                changes_made = 1

            # 检查是否有实际更改
            if new_content == original_content:
                execution_time = time.time() - start_time
                result_dict = {
                    "success": False,
                    "error": "No changes were made to the file",
                    "changes_made": 0
                }
                return ToolRequestResult(
                    request=tool_request,
                    result=result_dict,
                    content=json.dumps(result_dict, ensure_ascii=False),
                    is_error=True,
                    execution_times=execution_time
                )



            # 生成差异报告
            diff = self._generate_diff(original_content, new_content, abs_file_path)

            # 原子写入：先写临时文件再 os.replace，避免写入中途崩溃/中断（Ctrl+C、
            # OOM、磁盘满）导致目标文件被截断丢失。与 WriteTool._atomic_write 一致。
            temp_fd, temp_path = tempfile.mkstemp(
                dir=os.path.dirname(abs_file_path), prefix='.tmp_edit_'
            )
            try:
                with os.fdopen(temp_fd, 'w', encoding='utf-8') as temp_file:
                    temp_file.write(new_content)
                    temp_file.flush()
                    os.fsync(temp_fd)
                os.replace(temp_path, abs_file_path)
            except Exception:
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass
                raise

            # 成功结果
            execution_time = time.time() - start_time
            result_dict = {
                "success": True,
                "changes_made": changes_made,
                "file_path": abs_file_path,
                "diff": diff,
                "message": f"Successfully made {changes_made} change(s) to {abs_file_path}"
            }
            
            return ToolRequestResult(
                request=tool_request,
                result=result_dict,
                content=json.dumps(result_dict, ensure_ascii=False),
                is_error=False,
                execution_times=execution_time
            )

        except UnicodeDecodeError:
            execution_time = time.time() - start_time
            result_dict = {
                "success": False,
                "error": "File could not be decoded as UTF-8 text",
                "changes_made": 0
            }
            return ToolRequestResult(
                request=tool_request,
                result=result_dict,
                content=json.dumps(result_dict, ensure_ascii=False),
                is_error=True,
                execution_times=execution_time
            )
        except PermissionError:
            execution_time = time.time() - start_time
            result_dict = {
                "success": False,
                "error": f"Permission denied when accessing file: {abs_file_path}",
                "changes_made": 0
            }
            return ToolRequestResult(
                request=tool_request,
                result=result_dict,
                content=json.dumps(result_dict, ensure_ascii=False),
                is_error=True,
                execution_times=execution_time
            )
        except Exception as e:
            execution_time = time.time() - start_time
            result_dict = {
                "success": False,
                "error": f"Unexpected error: {str(e)}",
                "changes_made": 0
            }
            return ToolRequestResult(
                request=tool_request,
                result=result_dict,
                content=json.dumps(result_dict, ensure_ascii=False),
                is_error=True,
                execution_times=execution_time
            )

    def _generate_diff(self, original: str, modified: str, file_path: str) -> str:
        """生成文件差异报告
        
        生成简洁的行号+diff格式报告，例如：
        Lines 10-12:
        - old line 1
        - old line 2
        + new line 1
        + new line 2
        """
        original_lines = original.splitlines()
        modified_lines = modified.splitlines()
        
        # 使用SequenceMatcher找出差异区域
        matcher = difflib.SequenceMatcher(None, original_lines, modified_lines)
        diff_output = []
        
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                # 相同的行，可以显示上下文
                continue
            elif tag == 'replace':
                # 替换操作
                start_line = i1 + 1
                end_line = i2
                diff_output.append(f"\nLines {start_line}-{end_line}:")
                for line in original_lines[i1:i2]:
                    diff_output.append(f"  - {line}")
                for line in modified_lines[j1:j2]:
                    diff_output.append(f"  + {line}")
            elif tag == 'delete':
                # 删除操作
                start_line = i1 + 1
                end_line = i2
                diff_output.append(f"\nLines {start_line}-{end_line}:")
                for line in original_lines[i1:i2]:
                    diff_output.append(f"  - {line}")
            elif tag == 'insert':
                # 插入操作
                insert_after = i1
                diff_output.append(f"\nAfter line {insert_after}:")
                for line in modified_lines[j1:j2]:
                    diff_output.append(f"  + {line}")
        
        return "\n".join(diff_output) if diff_output else "No changes"
    
    def _generate_unified_diff(self, original: str, modified: str, file_path: str) -> str:
        """生成标准的unified diff格式报告（备用方法）"""
        original_lines = original.splitlines(keepends=True)
        modified_lines = modified.splitlines(keepends=True)

        diff = difflib.unified_diff(
            original_lines,
            modified_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            lineterm=""
        )

        return "".join(diff)


