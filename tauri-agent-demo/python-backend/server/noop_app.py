from typing import Any, Callable


Decorator = Callable[[Callable[..., Any]], Callable[..., Any]]


class NoopFastAPI:
    def _identity(self, *_args: Any, **_kwargs: Any) -> Decorator:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            return func

        return decorator

    get = _identity
    post = _identity
    put = _identity
    delete = _identity
    patch = _identity
    websocket = _identity
    on_event = _identity

    def add_middleware(self, *_args: Any, **_kwargs: Any) -> None:
        return None
