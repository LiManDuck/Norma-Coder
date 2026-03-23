# Norma-Coder
使用python 实现claude code 的cli 助手


# 使用


## cli使用
**安装依赖**

```bash
git clone 本项目
pip install -r requirements.txt

pip install -e .

终端执行 norma ,显示输入框则安装成功
```

模型配置
在 ~/.norma/config.json下配置 openai模型接口
```json
{
  "model": "",
  "api_key": "",
  "base_url": "",
}

```

## python端使用

```
from norma.core.agent_types import BaseAgent
from norma.agent.norma_coder import (

    NormaArtifact,
    NormaCoder
)
from norma.core.openai_llm import OpenAILLM
from norma.util.console import AgentConsole

openai_llm  = OpenAILLM(

    model= "r",
    api_key="sk-xxx",
    base_url= ""
)

asyncio.run(


        NormaCoder(
            llm= openai_llm,
            cwd = '/usr2/cli_ability_test/ucl_workspace '  # 输入工作目录


        ).run(task= '你的具体的任务')
)

```


# 复现参考

- https://www.anthropic.com/engineering/claude-think-tool
- https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/

##  claude code的prompt参考
- https://github.com/Yuyz0112/claude-code-reverse/ :
- https://minusx.ai/blog/decoding-claude-code