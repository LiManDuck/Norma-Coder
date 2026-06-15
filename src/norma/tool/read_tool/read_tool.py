"""
Read Tool - 继承自 Tool 基类的实现

复现说明:
本文件基于 /usr2/TaiyiAgent/ref/claude-code-reverse/results/tools/Read.tool.yaml 中的description字段内容
该description包含了Read工具的完整功能说明和使用指南,包括:
1. 从本地文件系统读取任意文件的能力
2. 必须使用绝对路径的要求
3. 读取行的限制
4. 可选的offset和limit参数用于大文件处理
5. 超过2000字符行的截断处理
6. 类似cat -n的带行号输出格式
7. 批量读取多文件的建议
8. 空文件的系统提醒处理
9. 只能读取文本文件的限制

该prompt被转化为Python字符串,用于指导AI如何正确使用Read工具。
"""

import os
import json
import mimetypes
from typing import Optional, Dict, Any
from pathlib import Path
import time

from norma.core.tool_types import (
    Tool,
    ToolSchema,
    ParametersSchema,
    ToolRequest,
    ToolRequestResult
)


Default_Max_Lines = 1000
DEFAULT_TOOL_PROMPT = f"""
读取本地文件系统中的文件。你可以直接使用本工具访问任意文件。
请假定本工具具备读取当前设备中所有文件的权限。若用户提供了文件路径,请默认该路径合法有效。读取不存在的文件并不会导致异常,工具会直接返回对应的错误信息。
使用规则:
- 路径要求:file_path(文件路径)参数必须传入绝对路径,不支持相对路径。
- 文件类型限制:本工具仅支持读取文本文件(源代码、配置文件、文档等),不支持二进制文件(图片、视频、可执行文件等)。
- 默认读取规则: 默认从文件起始位置开始,最多读取{Default_Max_Lines}行内容。
- 分段读取配置: 建议指定line offset(行偏移量)和limit(读取行数限制),该配置对大文件尤为实用；若无需分段,建议保持默认配置以读取完整文件。
- 行内容截断规则:任意单行内容长度超过2000 字符时,超出部分将被自动截断。
- 输出格式:结果以cat -n命令的格式返回,行号从1开始计数。
- 批量调用能力:你可以在单次响应中调用多个工具。当存在多个潜在有用的文件时,建议主动批量读取,提升分析效率。
- 空文件处理:若读取的文件存在但内容为空,工具不会返回文件内容,而是返回一条系统提醒。
核心注意事项:
- 始终使用绝对路径,避免使用相对路径。
- 仅支持文本文件,二进制文件会返回错误。
- 处理大文件时,建议合理配置偏移量与读取行数限制。
- 空文件会触发系统提醒,而非空内容返回。
- 当存在多个相关文件时,鼓励批量读取以提升效率。
"""


class ReadTool(Tool):
    """Read工具类 - 继承自Tool基类,仅支持读取文本文件"""


 

    @property
    def name(self) -> str:
        return "Read"

    @property
    def description(self) -> str:
        return DEFAULT_TOOL_PROMPT

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
                        "description": "Absolute path to the file to read"
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Line number to start reading from (optional, for large files)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of lines to read (optional, for large files)"
                    }
                },
                required=["file_path"]
            ),
            strict=False
        )

    def __init__(self):
        """初始化ReadTool,定义支持的文本文件扩展名"""
        # 常见的文本文件扩展名白名单
        self.text_file_extensions = {
            # 编程语言
            '.py', '.pyw', '.pyx', '.pyd',  # Python
            '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs',  # JavaScript/TypeScript
            '.java', '.class', '.jar',  # Java
            '.c', '.h', '.cpp', '.hpp', '.cc', '.cxx', '.c++', '.hh',  # C/C++
            '.cs', '.csx',  # C#
            '.go', '.mod', '.sum',  # Go
            '.rs', '.rlib',  # Rust
            '.rb', '.rbw', '.rake', '.gemspec',  # Ruby
            '.php', '.phtml', '.php3', '.php4', '.php5',  # PHP
            '.swift',  # Swift
            '.kt', '.kts',  # Kotlin
            '.scala', '.sc',  # Scala
            '.m', '.mm',  # Objective-C
            '.r', '.R', '.rdata', '.rds',  # R
            '.lua',  # Lua
            '.pl', '.pm', '.t', '.pod',  # Perl
            '.sh', '.bash', '.zsh', '.fish', '.ksh', '.csh',  # Shell
            '.bat', '.cmd', '.ps1', '.psm1',  # Windows scripts
            
            # 标记语言和配置
            '.html', '.htm', '.xhtml',  # HTML
            '.xml', '.xsd', '.xsl', '.xslt', '.dtd',  # XML
            '.json', '.jsonc', '.json5',  # JSON
            '.yaml', '.yml',  # YAML
            '.toml',  # TOML
            '.ini', '.cfg', '.conf', '.config',  # 配置文件
            '.properties', '.prop',  # Properties
            '.env', '.envrc',  # 环境变量
            
            # 文档和标记
            '.md', '.markdown', '.mdown', '.mkd',  # Markdown
            '.rst', '.rest',  # reStructuredText
            '.tex', '.latex', '.ltx',  # LaTeX
            '.txt', '.text',  # 纯文本
            '.csv', '.tsv',  # 表格数据
            '.log',  # 日志文件
            
            # 数据和序列化
            '.sql',  # SQL
            '.graphql', '.gql',  # GraphQL
            '.proto',  # Protocol Buffers
            '.thrift',  # Thrift
            
            # Web
            '.css', '.scss', '.sass', '.less', '.styl',  # CSS
            '.vue', '.svelte',  # 前端框架
            
            # 其他
            '.gitignore', '.gitattributes', '.gitmodules',  # Git
            '.dockerignore',  # Docker
            '.editorconfig',  # Editor Config
            'Dockerfile', 'Makefile', 'Rakefile', 'Gemfile',  # 无扩展名但是文本文件
        }
        
        # 纯文本MIME类型
        self.text_mime_types = {
            'text/',  # 所有以text/开头的类型
            'application/json',
            'application/xml',
            'application/javascript',
            'application/x-sh',
            'application/x-python',
        }

    def is_text_file(self, file_path: str) -> tuple[bool, Optional[str]]:
        """
        判断文件是否为文本文件
        
        返回: (是否为文本文件, 错误信息)
        """
        # 1. 首先检查扩展名
        file_ext = Path(file_path).suffix.lower()
        file_name = Path(file_path).name
        
        # 无扩展名的特殊文件(如Dockerfile, Makefile等)
        if not file_ext and file_name in {'Dockerfile', 'Makefile', 'Rakefile', 'Gemfile', 
                                           'CMakeLists.txt', 'requirements.txt'}:
            return True, None
        
        if file_ext in self.text_file_extensions:
            return True, None
        
        # 2. 使用mimetypes库检查
        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type:
            # 检查是否是文本类型
            if any(mime_type.startswith(text_type) for text_type in self.text_mime_types):
                return True, None
        
        # 3. 读取文件的前几KB,检查是否包含null字节(二进制文件的特征)
        try:
            with open(file_path, 'rb') as f:
                # 读取前8KB
                sample = f.read(8192)
                
            # 空文件视为文本文件
            if not sample:
                return True, None
            
            # 检查是否包含null字节
            if b'\x00' in sample:
                return False, "File appears to be binary (contains null bytes)"
            
            # 检查文本字符比例
            # 可打印ASCII字符、换行符、制表符等
            text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x7f)) | set(range(0x80, 0x100)))
            non_text_count = sum(1 for byte in sample if byte not in text_chars)
            
            # 如果非文本字符超过30%,判定为二进制文件
            if non_text_count / len(sample) > 0.3:
                return False, "File appears to be binary (high ratio of non-text characters)"
            
            # 通过所有检查,认为是文本文件
            return True, None
            
        except Exception as e:
            return False, f"Error checking file type: {str(e)}"

    async def execute(self, tool_request: ToolRequest) -> ToolRequestResult:
        """执行Read工具"""
        start_time = time.time()
        
        # 从ToolRequest中提取参数
        if isinstance(tool_request.tool_call_arguments, str):
            call_args = self.parse_string_arguments(tool_request.tool_call_arguments)
        else:
            call_args = tool_request.tool_call_arguments
        
        # 验证必需参数
        if "file_path" not in call_args:
            execution_time = time.time() - start_time
            error_result = {"error": "Missing required parameter: file_path"}
            return ToolRequestResult(
                request=tool_request,
                result=error_result,
                content=json.dumps(error_result, ensure_ascii=False),
                is_error=True,
                execution_times=execution_time
            )

        # 执行读取操作
        try:
            result = self._read_file(
                file_path=call_args["file_path"],
                offset=call_args.get("offset"),
                limit=call_args.get("limit")
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

    def _read_file(self, file_path: str, offset: Optional[int] = None,
                   limit: Optional[int] = None) -> Dict[str, Any]:
        """读取文件内容的核心逻辑"""
        # 转换为绝对路径
        file_path = os.path.abspath(file_path)

        # 检查文件是否存在
        if not os.path.exists(file_path):
            return {
                "success": False,
                "error": f"File does not exist: {file_path}"
            }

        # 检查是否为文件
        if not os.path.isfile(file_path):
            return {
                "success": False,
                "error": f"Path is not a file: {file_path}"
            }

        # 检查文件权限
        if not os.access(file_path, os.R_OK):
            return {
                "success": False,
                "error": f"Permission denied: cannot read file {file_path}"
            }

        # 检查是否为文本文件
        is_text, error_msg = self.is_text_file(file_path)
        if not is_text:
            return {
                "success": False,
                "error": f"File is not a text file: {error_msg or 'unsupported file type'}"
            }

        # 调用文本读取方法
        return self._read_text(file_path, offset, limit)

    def _read_text(self, file_path: str, offset: Optional[int] = None,
                   limit: Optional[int] = None) -> Dict[str, Any]:
        """读取文本文件"""
        try:
            # 尝试以UTF-8编码读取
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

        except UnicodeDecodeError:
            # 尝试其他常见编码
            for encoding in ['gbk', 'gb2312', 'latin-1', 'iso-8859-1']:
                try:
                    with open(file_path, 'r', encoding=encoding) as f:
                        lines = f.readlines()
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            else:
                return {
                    "success": False,
                    "error": "File could not be decoded with any supported encoding (UTF-8, GBK, Latin-1)"
                }
        
        except Exception as e:
            return {
                "success": False,
                "error": f"Error reading file: {str(e)}"
            }

        # 应用offset和limit参数
        if offset is not None:
            if offset < 1:
                offset = 1
            start_index = offset - 1
        else:
            start_index = 0

        if limit is not None:
            if limit < 1:
                limit = Default_Max_Lines
            end_index = start_index + limit
        else:
            # 默认读取Default_Max_Lines行
            end_index = start_index + Default_Max_Lines

        # 截取指定范围的行
        selected_lines = lines[start_index:end_index]

        # 处理超长行的截断
        truncated_lines = []
        for i, line in enumerate(selected_lines, start=start_index + 1):
            if len(line) > 2000:
                line = line[:2000] + "... (line truncated)\n"
            truncated_lines.append(f"{i:4d}\t{line.rstrip()}")

        # 格式化为cat -n风格输出
        content = "\n".join(truncated_lines)

        # 处理空文件
        if not content.strip():
            return {
                "success": True,
                "content": "<system-reminder>The file exists but has empty contents</system-reminder>",
                "line_count": 0,
                "file_type": "text",
                "message": "File exists but is empty",
                "file_path": file_path
            }

        return {
            "success": True,
            "content": content,
            "line_count": len(selected_lines),
            "file_type": "text",
            "total_lines": len(lines),
            "offset": offset or 1,
            "limit": limit or len(selected_lines),
            "file_path": file_path
        }


# 使用示例
if __name__ == "__main__":
    import asyncio
    
    async def test_read_tool():
        """测试ReadTool"""
        tool = ReadTool()
        
        # 创建测试文件
        test_file = "/tmp/test_read.txt"
        with open(test_file, 'w', encoding='utf-8') as f:
            for i in range(100):
                f.write(f"This is line {i+1}\n")
        
        # 测试1: 读取整个文件(前1000行)
        print("Test 1: Read entire file")
        request = ToolRequest(
            tool_call_id="test1",
            tool_call_name="Read",
            tool_call_arguments={"file_path": test_file}
        )
        result = await tool.execute(request)
        print(f"Is Error: {result.is_error}")
        print(f"Content (first 200 chars): {result.content[:200]}...\n")
        
        # 测试2: 使用offset和limit
        print("Test 2: Read with offset and limit")
        request = ToolRequest(
            tool_call_id="test2",
            tool_call_name="Read",
            tool_call_arguments={
                "file_path": test_file,
                "offset": 10,
                "limit": 5
            }
        )
        result = await tool.execute(request)
        print(f"Is Error: {result.is_error}")
        print(f"Content: {result.content}\n")
        
        # 测试3: 读取不存在的文件
        print("Test 3: Read non-existent file")
        request = ToolRequest(
            tool_call_id="test3",
            tool_call_name="Read",
            tool_call_arguments={"file_path": "/tmp/nonexistent.txt"}
        )
        result = await tool.execute(request)
        print(f"Is Error: {result.is_error}")
        print(f"Content: {result.content}\n")
        
        # 测试4: 创建二进制文件并尝试读取
        print("Test 4: Read binary file")
        binary_file = "/tmp/test_binary.bin"
        with open(binary_file, 'wb') as f:
            f.write(b'\x00\x01\x02\x03\x04\x05')
        
        request = ToolRequest(
            tool_call_id="test4",
            tool_call_name="Read",
            tool_call_arguments={"file_path": binary_file}
        )
        result = await tool.execute(request)
        print(f"Is Error: {result.is_error}")
        print(f"Content: {result.content}\n")
        
        # 清理测试文件
        os.remove(test_file)
        os.remove(binary_file)
    
    asyncio.run(test_read_tool())
