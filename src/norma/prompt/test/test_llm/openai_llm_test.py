from typing import Dict,Any,Literal,List
from pydantic import BaseModel
import logging
from pprint import pprint
logging.basicConfig(level= logging.INFO)


from norma.core.llm_types import (

    BaseLLM,
    LLMMessage,
    UserMessage,
    LLMRequest,
    LLMResponse
)
from norma.core.tool_types import (
    Tool,
    ToolRequest,
    FunctionTool
    
)
from norma.core.openai_llm import OpenAILLM 




from norma.config.model_config import HW_LLM_CONFIG
import logging
logging.basicConfig(level= logging.INFO)



import os
os.environ['no_proxy']="7.242.99.159"

rtos_llm  = OpenAILLM(


)

def get_weather(location: str, time: Literal['当前时间','过去']) -> Dict[str, Any]:
    """获取天气状况"""
    return {
        "location": location,
        "temperature": 22,
        "time": time,
        "condition": "sunny"
    }
    
wearther_tool = FunctionTool(func= get_weather)


class WeatherOutput(BaseModel):

    location: str
    weather_status: str 


class LLMTEST:




    def __init__(
        self,
        test_llm: BaseLLM,
        test_funcs : List[Tool],
        test_schema:  Any
    ):

        self.llm = test_llm
        self.tools = test_funcs

  

    


    async def test_function_call(self):
       
       
        llmrequest : LLMRequest = LLMRequest(
            messages=[UserMessage(content="北京天气如何？")],
            tools=self.tools,
            stream_mode= False
        )

        
        response = await self.llm.chat(
           llmrequest
        )

        print(response)

        
        
    
        print('非流式测试完成')
        print('测试流式')
        llmrequest.stream_mode = True
        response_stream =   self.llm.stream_chat(llmrequest)
        

        async for chunk_response in response_stream:

            print(chunk_response)
            continue
            if isinstance(chunk_response.content, str):
                new_text = chunk_response.content[len(full_response):]
                if new_text:
                    print(new_text, end="", flush=True)
                    full_response = chunk_response.content
            final_response = chunk_response



    



    async def test_stream_mode(self):
       
        

        response_stream = await self.llm.stream_chat(messages=[  UserMessage(content="讲一个简短的笑话。")])
        full_response = ""

        async for chunk_response in response_stream:
            if isinstance(chunk_response.content, str):
                new_text = chunk_response.content[len(full_response):]
                if new_text:
                    print(new_text, end="", flush=True)
                    full_response = chunk_response.content
            final_response = chunk_response




    async def test_json_outpiut(self, ):


        class outputtye(BaseModel):

            joke_name: str
            joke_content: str 


            
       
        llmrequest : LLMRequest = LLMRequest(
            messages=[UserMessage(content="讲一个简短的笑话。")],
            tools=self.tools,
            stream_mode= False,
            structured_output= outputtye,
        )
        response: LLMResponse = await self.llm.chat(llmrequest
                                                    )
        print(response)
        

        
        

if __name__ == "__main__":  
    

    import asyncio
    test_case = LLMTEST(
        test_llm=  rtos_llm ,
        test_funcs= [wearther_tool],
        test_schema= None
    )

    asyncio.run(test_case.test_json_outpiut())
