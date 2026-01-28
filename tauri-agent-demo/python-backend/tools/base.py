"""
Tool System Base Classes

This module defines the core abstractions for the tool system:
- ToolParameter: Defines tool input parameters
- Tool: Abstract base class for all tools
- ToolRegistry: Central registry for tool management
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from dataclasses import dataclass


class ToolParameter(BaseModel):
    """Defines a single parameter for a tool"""
    name: str
    type: str  # "string", "number", "boolean", "object", "array"
    description: str
    required: bool = True
    default: Any = None


class Tool(ABC):
    """
    Abstract base class for all tools.
    
    Tools extend agent capabilities by providing external functionality:
    - Calculator: Mathematical computations
    - Weather: Weather information (mock for now)
    - Search: Web search (mock for now)
    - Custom tools: User-defined functionality
    """
    
    def __init__(self):
        self.name: str = ""
        self.description: str = ""
        self.parameters: List[ToolParameter] = []
    
    @abstractmethod
    async def execute(self, input_data: str) -> str:
        """
        Execute the tool with given input.
        
        Args:
            input_data: Tool input (usually string, parsed by tool)
        
        Returns:
            Tool execution result as string
        
        Raises:
            ValueError: If input is invalid
            RuntimeError: If execution fails
        """
        pass
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert tool to dictionary format for LLM.
        
        Returns:
            Dict with tool metadata for LLM prompt
        """
        return {
            "name": self.name,
            "description": self.description,
            "parameters": [p.dict() for p in self.parameters]
        }
    
    def validate_input(self, input_data: str) -> bool:
        """
        Validate tool input.
        
        Args:
            input_data: Input to validate
        
        Returns:
            True if valid, False otherwise
        """
        # Basic validation - subclasses can override
        return bool(input_data and input_data.strip())


class ToolRegistry:
    """
    Central registry for tool management.
    
    Provides:
    - Tool registration
    - Tool discovery
    - Tool lookup by name
    """
    
    _tools: Dict[str, Tool] = {}
    
    @classmethod
    def register(cls, tool: Tool):
        """
        Register a tool in the registry.
        
        Args:
            tool: Tool instance to register
        
        Raises:
            ValueError: If tool with same name already exists
        """
        if tool.name in cls._tools:
            raise ValueError(f"Tool '{tool.name}' already registered")
        cls._tools[tool.name] = tool
    
    @classmethod
    def unregister(cls, tool_name: str):
        """
        Unregister a tool.
        
        Args:
            tool_name: Name of tool to unregister
        """
        if tool_name in cls._tools:
            del cls._tools[tool_name]
    
    @classmethod
    def get(cls, tool_name: str) -> Optional[Tool]:
        """
        Get tool by name.
        
        Args:
            tool_name: Name of tool to retrieve
        
        Returns:
            Tool instance or None if not found
        """
        return cls._tools.get(tool_name)
    
    @classmethod
    def get_all(cls) -> List[Tool]:
        """
        Get all registered tools.
        
        Returns:
            List of all registered tools
        """
        return list(cls._tools.values())
    
    @classmethod
    def clear(cls):
        """Clear all registered tools (mainly for testing)"""
        cls._tools.clear()
    
    @classmethod
    def list_names(cls) -> List[str]:
        """
        Get names of all registered tools.
        
        Returns:
            List of tool names
        """
        return list(cls._tools.keys())
