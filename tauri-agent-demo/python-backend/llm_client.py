from typing import Optional, List, Dict, Any
import httpx
from models import LLMConfig, LLMApiType

class LLMClient:
    """统一的 LLM 客户端，支持多种 API"""
    
    def __init__(self, config: LLMConfig):
        self.config = config
        self.timeout = 60.0
    
    async def chat(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        发送聊天请求到 LLM
        
        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
        
        Returns:
            包含完整响应的字典，包括 content 和原始响应数据
            {
                "content": str,  # LLM 的回复内容
                "raw_response": dict  # 完整的原始响应
            }
        """
        if self.config.api_type == "openai":
            return await self._chat_openai(messages)
        elif self.config.api_type == "zhipu":
            return await self._chat_zhipu(messages)
        elif self.config.api_type == "deepseek":
            return await self._chat_deepseek(messages)
        else:
            raise ValueError(f"不支持的 API 类型: {self.config.api_type}")
    
    async def chat_stream(self, messages: List[Dict[str, str]]):
        """
        流式发送聊天请求到 LLM
        
        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
        
        Yields:
            str: 逐个生成的文本片段（chunk）
        """
        if self.config.api_type == "openai":
            async for chunk in self._chat_openai_stream(messages):
                yield chunk
        elif self.config.api_type == "zhipu":
            async for chunk in self._chat_zhipu_stream(messages):
                yield chunk
        elif self.config.api_type == "deepseek":
            async for chunk in self._chat_deepseek_stream(messages):
                yield chunk
        else:
            raise ValueError(f"不支持的 API 类型: {self.config.api_type}")
    
    async def _chat_openai(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """OpenAI API 调用"""
        base_url = self.config.base_url or "https://api.openai.com/v1"
        
        request_payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens
        }
        
        # 🔥 如果是推理模型（O1/GPT-5 系列），添加 reasoning 参数
        model_lower = self.config.model.lower()
        if "o1" in model_lower or "gpt-5" in model_lower:
            # 推理模型不支持 temperature
            request_payload.pop("temperature", None)
            
            # 使用新的 reasoning 对象格式
            reasoning_effort = getattr(self.config, 'reasoning_effort', 'medium')
            reasoning_summary = getattr(self.config, 'reasoning_summary', 'detailed')
            
            request_payload["reasoning"] = {
                "effort": reasoning_effort,
                "summary": reasoning_summary
            }
            
            print(f"🧠 [Reasoning Mode] effort={reasoning_effort}, summary={reasoning_summary}")
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json"
                },
                json=request_payload
            )
            response.raise_for_status()
            data = response.json()
            
            # 提取推理 tokens 信息（如果有）
            usage = data.get("usage", {})
            reasoning_tokens = usage.get("reasoning_tokens", 0)
            
            if reasoning_tokens > 0:
                print(f"🧠 [Reasoning Tokens] {reasoning_tokens} tokens used for reasoning")
            
            return {
                "content": data["choices"][0]["message"]["content"],
                "raw_response": data,
                "reasoning_tokens": reasoning_tokens
            }
    
    async def _chat_openai_stream(self, messages: List[Dict[str, str]]):
        """OpenAI API 流式调用"""
        import json
        
        base_url = self.config.base_url or "https://api.openai.com/v1"
        
        request_payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": True
        }
        
        # 🔥 如果是推理模型（O1/GPT-5 系列），添加 reasoning 参数
        model_lower = self.config.model.lower()
        if "o1" in model_lower or "gpt-5" in model_lower:
            # 推理模型不支持 temperature
            request_payload.pop("temperature", None)
            
            # 使用新的 reasoning 对象格式
            reasoning_effort = getattr(self.config, 'reasoning_effort', 'medium')
            reasoning_summary = getattr(self.config, 'reasoning_summary', 'detailed')
            
            request_payload["reasoning"] = {
                "effort": reasoning_effort,
                "summary": reasoning_summary
            }
            
            print(f"🧠 [Reasoning Mode Stream] effort={reasoning_effort}, summary={reasoning_summary}")
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json"
                },
                json=request_payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0]["delta"]
                            if "content" in delta:
                                yield delta["content"]
                        except (json.JSONDecodeError, KeyError):
                            continue
    
    async def _chat_zhipu(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """智谱 AI API 调用"""
        base_url = self.config.base_url or "https://open.bigmodel.cn/api/paas/v4"
        
        request_payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens
        }
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json"
                },
                json=request_payload
            )
            response.raise_for_status()
            data = response.json()
            return {
                "content": data["choices"][0]["message"]["content"],
                "raw_response": data
            }
    
    async def _chat_zhipu_stream(self, messages: List[Dict[str, str]]):
        """智谱 AI API 流式调用"""
        import json
        
        base_url = self.config.base_url or "https://open.bigmodel.cn/api/paas/v4"
        
        request_payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": True
        }
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json"
                },
                json=request_payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0]["delta"]
                            if "content" in delta:
                                yield delta["content"]
                        except (json.JSONDecodeError, KeyError):
                            continue
    
    async def _chat_deepseek(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """Deepseek API 调用"""
        base_url = self.config.base_url or "https://api.deepseek.com/v1"
        
        request_payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens
        }
        
        # 🔥 如果是 deepseek-reasoner 模型，不要设置 temperature
        if "reasoner" in self.config.model.lower():
            request_payload.pop("temperature", None)
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json"
                },
                json=request_payload
            )
            response.raise_for_status()
            data = response.json()
            
            # 提取内容（可能包含 reasoning_content）
            message = data["choices"][0]["message"]
            content = message.get("content", "")
            reasoning_content = message.get("reasoning_content", "")
            
            # 如果有推理内容，合并显示
            if reasoning_content:
                full_content = f"[推理过程]\n{reasoning_content}\n\n[回答]\n{content}"
            else:
                full_content = content
            
            return {
                "content": full_content,
                "raw_response": data
            }
    
    async def _chat_deepseek_stream(self, messages: List[Dict[str, str]]):
        """DeepSeek API 流式调用"""
        import json
        
        base_url = self.config.base_url or "https://api.deepseek.com/v1"
        
        request_payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": True
        }
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json"
                },
                json=request_payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0]["delta"]
                            if "content" in delta:
                                yield delta["content"]
                        except (json.JSONDecodeError, KeyError):
                            continue

def create_llm_client(config: LLMConfig) -> LLMClient:
    """创建 LLM 客户端实例"""
    return LLMClient(config)
