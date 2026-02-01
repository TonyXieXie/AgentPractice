from typing import List, Dict, Any
import re

class MessageProcessor:
    """消息预处理和后处理"""
    
    @staticmethod
    def preprocess_user_message(content: str, history: List[Dict[str, str]] = None) -> str:
        """
        预处理用户输入
        
        Args:
            content: 用户输入的原始内容
            history: 历史消息（可选）
        
        Returns:
            处理后的消息内容
        """
        # 1. 清理空白字符
        content = content.strip()
        
        # 2. 移除多余的空行
        content = re.sub(r'\n{3,}', '\n\n', content)
        
        # 可以在这里添加更多预处理逻辑，例如：
        # - 添加系统提示词
        # - 格式化特殊命令
        # - 添加上下文信息
        
        return content
    
    @staticmethod
    def build_messages_for_llm(
        user_message: str,
        history: List[Dict[str, str]] = None,
        system_prompt: str = None,
        max_history: int = 10,
        system_role: str = "system"
    ) -> List[Dict[str, str]]:
        """
        构建发送给 LLM 的消息列表
        
        Args:
            user_message: 当前用户消息
            history: 历史消息
            system_prompt: 系统提示词
            max_history: 最大历史消息数量
        
        Returns:
            格式化的消息列表
        """
        messages = []
        
        # 添加系统提示词
        if system_prompt:
            messages.append({"role": system_role, "content": system_prompt})
        
        # 添加历史消息（限制数量）
        if history:
            recent_history = history[-max_history:] if len(history) > max_history else history
            messages.extend(recent_history)
        
        # 添加当前用户消息
        messages.append({"role": "user", "content": user_message})
        
        return messages
    
    @staticmethod
    def postprocess_llm_response(content: str) -> str:
        """
        后处理 LLM 响应
        
        Args:
            content: LLM 返回的原始内容
        
        Returns:
            处理后的响应内容
        """
        # 1. 清理空白字符
        content = content.strip()
        
        # 2. 移除可能的 markdown 代码块标记（如果不需要）
        # content = re.sub(r'^```[\w]*\n|```$', '', content, flags=re.MULTILINE)
        
        # 可以在这里添加更多后处理逻辑，例如：
        # - 格式化输出
        # - 过滤敏感内容
        # - 添加额外信息
        # - 转换特殊格式
        
        return content
    
    @staticmethod
    def format_history_for_display(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        格式化历史消息用于前端显示
        
        Args:
            messages: 数据库中的消息列表
        
        Returns:
            格式化后的消息列表
        """
        formatted = []
        for msg in messages:
            formatted.append({
                "id": msg.get("id"),
                "role": msg.get("role"),
                "content": msg.get("content"),
                "timestamp": msg.get("timestamp")
            })
        return formatted

# 创建全局处理器实例
message_processor = MessageProcessor()
