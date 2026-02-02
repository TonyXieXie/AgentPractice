"""
Built-in Tools

This module provides basic tools that are included by default:
- CalculatorTool: Mathematical calculations
- WeatherTool: Weather information (mock)
- TavilySearchTool: Web search (Tavily)
- ReadFileTool / WriteFileTool: Project file access
- RunShellTool: Shell execution with allowlist
"""

from ..base import Tool, ToolParameter
from ..config import is_tool_enabled
from .system_tools import ReadFileTool, WriteFileTool, RunShellTool, TavilySearchTool


class CalculatorTool(Tool):
    """
    Tool for performing mathematical calculations.
    
    Supports basic arithmetic operations using Python's eval in a safe sandbox.
    """
    
    def __init__(self):
        super().__init__()
        self.name = "calculator"
        self.description = "Execute mathematical calculations. Input should be a valid mathematical expression like '2+3*4' or '(10-5)/2'."
        self.parameters = [
            ToolParameter(
                name="expression",
                type="string",
                description="Mathematical expression to evaluate",
                required=True
            )
        ]
    
    async def execute(self, expression: str) -> str:
        """
        Execute mathematical calculation.
        
        Args:
            expression: Mathematical expression (e.g., "2+3*4")
        
        Returns:
            Result of calculation as string
        
        Raises:
            ValueError: If expression is invalid
        """
        try:
            # Safe evaluation - no builtins, no imports
            result = eval(expression, {"__builtins__": {}}, {})
            return str(result)
        except SyntaxError as e:
            raise ValueError(f"Invalid mathematical expression: {e}")
        except Exception as e:
            raise ValueError(f"Calculation error: {e}")


class WeatherTool(Tool):
    """
    Tool for querying weather information.
    
    NOTE: Currently returns mock data. Replace with real API integration later.
    """
    
    def __init__(self):
        super().__init__()
        self.name = "weather"
        self.description = "Query current weather for a specified city. Input should be the city name."
        self.parameters = [
            ToolParameter(
                name="city",
                type="string",
                description="City name (e.g., 'Beijing', 'London')",
                required=True
            )
        ]
    
    async def execute(self, city: str) -> str:
        """
        Get weather for specified city.
        
        Args:
            city: City name
        
        Returns:
            Weather information as string
        """
        # TODO: Replace with real weather API integration
        # For now, return mock data
        mock_weather = {
            "beijing": "Beijing: Sunny, Temperature: 18°C, Humidity: 45%, Wind: 10 km/h",
            "london": "London: Cloudy, Temperature: 12°C, Humidity: 70%, Wind: 15 km/h",
            "tokyo": "Tokyo: Rainy, Temperature: 15°C, Humidity: 80%, Wind: 8 km/h",
        }
        
        city_lower = city.lower()
        if city_lower in mock_weather:
            return mock_weather[city_lower]
        else:
            return f"{city}: Partly cloudy, Temperature: 20°C, Humidity: 60%, Wind: 12 km/h (Mock data - API integration pending)"


# Register tools automatically on import
from ..base import ToolRegistry

def register_builtin_tools():
    """Register all built-in tools in the registry"""
    if is_tool_enabled("calculator"):
        ToolRegistry.register(CalculatorTool())
    if is_tool_enabled("weather"):
        ToolRegistry.register(WeatherTool())
    if is_tool_enabled("search"):
        ToolRegistry.register(TavilySearchTool())
    if is_tool_enabled("read_file"):
        ToolRegistry.register(ReadFileTool())
    if is_tool_enabled("write_file"):
        ToolRegistry.register(WriteFileTool())
    if is_tool_enabled("run_shell"):
        ToolRegistry.register(RunShellTool())
