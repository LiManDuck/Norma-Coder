"""
Repo Memory系统

为每个repo维护一个"笔记本"，记录：
- 代码结构理解
- 重要发现
- 待办事项
- 学到的模式

设计要点：
- 以repo为key的字典
- 简单的字符串存储
- 支持持久化到磁盘
- 模型自主管理内容
"""
import json
from typing import Dict, Any, Optional
from pathlib import Path
from datetime import datetime
from pydantic import BaseModel, Field
import logging

logger = logging.getLogger(__name__)


class RepoMemory(BaseModel):
    """
    单个Repo的记忆（笔记本）
    
    模型可以在这里记录任何有用的信息
    """
    repo_name: str
    content: str = ""
    last_updated: str = Field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    def update(self, new_content: str) -> Dict[str, Any]:
        """
        更新记忆内容（替换）
        
        Args:
            new_content: 新的记忆内容
        
        Returns:
            操作结果
        """
        self.content = new_content
        self.last_updated = datetime.now().isoformat()
        
        logger.info(f"Updated memory for repo '{self.repo_name}' ({len(new_content)} chars)")
        
        return {
            "status": "success",
            "message": f"Memory for repo '{self.repo_name}' updated successfully",
            "content_length": len(new_content)
        }
    
    def append(self, additional_content: str) -> Dict[str, Any]:
        """
        追加内容到记忆
        
        Args:
            additional_content: 要追加的内容
        
        Returns:
            操作结果
        """
        if self.content:
            self.content += "\n\n" + additional_content
        else:
            self.content = additional_content
        
        self.last_updated = datetime.now().isoformat()
        
        logger.info(f"Appended to memory for repo '{self.repo_name}' ({len(additional_content)} chars)")
        
        return {
            "status": "success",
            "message": f"Content appended to repo '{self.repo_name}' memory",
            "total_length": len(self.content)
        }
    
    def get_content(self) -> str:
        """
        获取记忆内容（供模型读取）
        
        Returns:
            格式化的记忆内容
        """
        if not self.content:
            return f"[No memory recorded for {self.repo_name} yet]"
        
        return f"""[Memory for {self.repo_name}]
Last Updated: {self.last_updated}

{self.content}"""
    
    def clear(self) -> Dict[str, Any]:
        """清空记忆"""
        self.content = ""
        self.last_updated = datetime.now().isoformat()
        
        return {
            "status": "success",
            "message": f"Memory for repo '{self.repo_name}' cleared"
        }
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return self.model_dump()
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RepoMemory":
        """从字典创建"""
        return cls(**data)


class RepoMemoryManager:
    """
    管理多个Repo的记忆
    
    功能：
    - 为每个repo维护独立的记忆
    - 持久化到磁盘
    - 自动加载和保存
    """
    
    def __init__(self, memory_dir: Optional[Path] = None):
        """
        初始化Memory Manager
        
        Args:
            memory_dir: 存储记忆文件的目录（默认~/.repo_agent_memory）
        """
        if memory_dir is None:
            self.memory_dir = Path.home() / '.repo_agent_memory'
        else:
            self.memory_dir = Path(memory_dir)
        
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        
        # 内存中的记忆缓存
        self.memories: Dict[str, RepoMemory] = {}
        
        logger.info(f"RepoMemoryManager initialized with dir: {self.memory_dir}")
    
    def get_memory(self, repo_name: str) -> RepoMemory:
        """
        获取或创建repo的记忆
        
        Args:
            repo_name: repo名称
        
        Returns:
            RepoMemory实例
        """
        if repo_name not in self.memories:
            # 尝试从磁盘加载
            memory_file = self.memory_dir / f"{repo_name}.json"
            
            if memory_file.exists():
                try:
                    with open(memory_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    self.memories[repo_name] = RepoMemory.from_dict(data)
                    logger.info(f"Loaded memory for repo '{repo_name}' from disk")
                except Exception as e:
                    logger.error(f"Failed to load memory for '{repo_name}': {e}")
                    self.memories[repo_name] = RepoMemory(repo_name=repo_name)
            else:
                # 创建新的记忆
                self.memories[repo_name] = RepoMemory(repo_name=repo_name)
                logger.info(f"Created new memory for repo '{repo_name}'")
        
        return self.memories[repo_name]
    
    def save_memory(self, repo_name: str) -> bool:
        """
        保存指定repo的记忆到磁盘
        
        Args:
            repo_name: repo名称
        
        Returns:
            是否保存成功
        """
        if repo_name not in self.memories:
            logger.warning(f"No memory found for repo '{repo_name}'")
            return False
        
        try:
            memory_file = self.memory_dir / f"{repo_name}.json"
            with open(memory_file, 'w', encoding='utf-8') as f:
                json.dump(
                    self.memories[repo_name].to_dict(), 
                    f, 
                    indent=2, 
                    ensure_ascii=False
                )
            
            logger.info(f"Saved memory for repo '{repo_name}' to {memory_file}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to save memory for '{repo_name}': {e}")
            return False
    
    def save_all(self) -> Dict[str, bool]:
        """
        保存所有记忆到磁盘
        
        Returns:
            每个repo的保存结果
        """
        results = {}
        for repo_name in self.memories:
            results[repo_name] = self.save_memory(repo_name)
        
        logger.info(f"Saved {sum(results.values())} / {len(results)} memories")
        return results
    
    def list_memories(self) -> Dict[str, Dict[str, Any]]:
        """
        列出所有记忆的摘要
        
        Returns:
            {repo_name: {last_updated, content_length, ...}}
        """
        summaries = {}
        for repo_name, memory in self.memories.items():
            summaries[repo_name] = {
                "last_updated": memory.last_updated,
                "content_length": len(memory.content),
                "has_content": bool(memory.content)
            }
        return summaries
    
    def get_all_contents(self) -> str:
        """
        获取所有repo的记忆内容（格式化为字符串）
        
        用于在system prompt中展示
        """
        if not self.memories:
            return "[No memories available]"
        
        parts = []
        for repo_name in sorted(self.memories.keys()):
            memory = self.memories[repo_name]
            parts.append(f"## {repo_name}\n{memory.get_content()}")
        
        return "\n\n".join(parts)
    
    def delete_memory(self, repo_name: str) -> Dict[str, Any]:
        """
        删除指定repo的记忆
        
        Args:
            repo_name: repo名称
        
        Returns:
            操作结果
        """
        # 从内存删除
        if repo_name in self.memories:
            del self.memories[repo_name]
        
        # 从磁盘删除
        memory_file = self.memory_dir / f"{repo_name}.json"
        if memory_file.exists():
            try:
                memory_file.unlink()
                logger.info(f"Deleted memory file for '{repo_name}'")
                return {
                    "status": "success",
                    "message": f"Memory for '{repo_name}' deleted"
                }
            except Exception as e:
                logger.error(f"Failed to delete memory file: {e}")
                return {
                    "status": "error",
                    "message": str(e)
                }
        
        return {
            "status": "success",
            "message": f"No memory found for '{repo_name}'"
        }


# ============ 工具函数 ============

def create_update_memory_func(memory: RepoMemory):
    """创建update_memory函数（用于FunctionTool）"""
    def update_memory(content: str) -> Dict[str, Any]:
        """
        Update the long-term memory/notes for this repository.
        
        Use this to record important information about:
        - Code structure and architecture
        - Key files and their purposes
        - Patterns and conventions
        - Tasks completed
        - Known issues or TODOs
        - Learnings from this session
        
        Args:
            content: New memory content (will replace existing content)
        
        Returns:
            Status of the operation
        """
        return memory.update(content)
    
    return update_memory


def create_append_memory_func(memory: RepoMemory):
    """创建append_memory函数（用于FunctionTool）"""
    def append_memory(content: str) -> Dict[str, Any]:
        """
        Append additional content to the repository memory.
        
        Use this to add new information without replacing existing notes.
        
        Args:
            content: Content to append
        
        Returns:
            Status of the operation
        """
        return memory.append(content)
    
    return append_memory
