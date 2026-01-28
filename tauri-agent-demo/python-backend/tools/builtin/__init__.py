"""
Built-in Tools

This module provides basic tools that are included by default:
- CalculatorTool: Mathematical calculations
- WeatherTool: Weather information (mock)
- SearchTool: Web search (mock)
"""

from ..base import Tool, ToolParameter


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


class SearchTool(Tool):
    """
    Tool for web search.
    
    NOTE: Currently returns mock data. Replace with real search API later.
    """
    
    def __init__(self):
        super().__init__()
        self.name = "search"
        self.description = "Search the web for information. Input should be the search query."
        self.parameters = [
            ToolParameter(
                name="query",
                type="string",
                description="Search query",
                required=True
            )
        ]
    
    async def execute(self, query: str) -> str:
        """
        Execute web search.
        
        Args:
            query: Search query
        
        Returns:
            Search results as string
        """
        # TODO: Replace with real search API (Google, Bing, or Perplexity)
        # For now, return mock results
        return f"Search results for '{query}':\n\n1. Mock result: Information about {query}\n2. Mock result: Latest updates on {query}\n3. Mock result: {query} - Wikipedia\n\n(Mock data - Search API integration pending)"


# Register tools automatically on import
from ..base import ToolRegistry

def register_builtin_tools():
    """Register all built-in tools in the registry"""
    ToolRegistry.register(CalculatorTool())
    ToolRegistry.register(WeatherTool())
    ToolRegistry.register(SearchTool())
