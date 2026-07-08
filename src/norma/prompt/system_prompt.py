
import os
from pathlib import Path



class SystemPromptService:

    def __init__(self) -> None:
        pass


    
    @staticmethod
    def get_claude_code_system_prompt( cwd: str | None = None)-> str:

        claude_code_md_file = Path(os.path.abspath(__file__)).parent / 'claude_code_system_prompt.md'
        claude_code_prompt =  claude_code_md_file.read_text(encoding= 'utf-8')
        if cwd is not None: 
            claude_code_prompt += f'\n# 当前任务环境\n 你当前处在{cwd}目录下,你可以访问该目录下的文件'


        return claude_code_prompt







if __name__ == "__main__":


    print( SystemPromptService.get_claude_code_system_prompt(cwd = '/usr2/kode-cli-python'))
