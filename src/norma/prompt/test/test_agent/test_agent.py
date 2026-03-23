




from norma.core.agent_types import BaseAgent
from norma.agent.norma_coder import (

    NormaArtifact,
    NormaCoder
)
from norma.core.openai_llm import OpenAILLM
from norma.util.console import AgentConsole 





class NormaCoderTester:

    def __init__(self,
    
        test_agent : NormaCoder
        ) -> None:
        

        self.norma_coder = test_agent
    

    

    async def run(self, task ): 

        await AgentConsole( 
            self.norma_coder.run(
                query= task
            )
        )

    
        
        

if __name__ == "__main__":

    import asyncio
    import os
    os.environ['no_proxy']="7.242.99.159"

    rtos_llm  = OpenAILLM(

        model= "GLM-4.5-Air",
        api_key="sk-hT0EiSE54tGfXXZKoAE9zg",
        base_url= "http://7.242.99.159:8050/v1"
    )

    asyncio.run(
        TaiyiCoderTester(

            test_agent= TaiyiCoder(
                llm= rtos_llm,
                cwd = '/usr2/cli_ability_test/ucl_workspace '

                
            )
        ).run(task= '阅读claude.md文件, 在当前的hm-ucl代码仓中实现ucl符号skb_network_header_len')
      # ).run(task= '阅读claude.md文件, 在当前的hm-ucl代码仓中实现ucl符号skb_network_header_len')
    )
